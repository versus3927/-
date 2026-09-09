import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/arena.db")

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS players(
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          game_id TEXT, nickname TEXT, games INTEGER NOT NULL DEFAULT 0,
          wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
          kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0,
          assists INTEGER NOT NULL DEFAULT 0, mvp INTEGER NOT NULL DEFAULT 0,
          points INTEGER NOT NULL DEFAULT 1000,
          PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS matches(
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
          league TEXT NOT NULL, map TEXT NOT NULL, host_id INTEGER NOT NULL,
          team_a TEXT NOT NULL, team_b TEXT NOT NULL,
          score_a INTEGER, score_b INTEGER, lobby_url TEXT,
          elo_a_delta INTEGER, elo_b_delta INTEGER,
          status TEXT NOT NULL DEFAULT 'waiting', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS result_submissions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
          match_id INTEGER NOT NULL, submitter_id INTEGER NOT NULL,
          score_a INTEGER NOT NULL, score_b INTEGER NOT NULL,
          elo_a_delta INTEGER, elo_b_delta INTEGER,
          screenshot_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          reviewer_id INTEGER, reason TEXT, analysis_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS config(
          guild_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS parties(
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
          leader_id INTEGER NOT NULL, league TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS party_members(
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, party_id INTEGER NOT NULL,
          PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS player_league_stats(
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, league TEXT NOT NULL,
          games INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
          losses INTEGER NOT NULL DEFAULT 0, kills INTEGER NOT NULL DEFAULT 0,
          deaths INTEGER NOT NULL DEFAULT 0, assists INTEGER NOT NULL DEFAULT 0,
          mvp INTEGER NOT NULL DEFAULT 0, points INTEGER NOT NULL DEFAULT 1000,
          PRIMARY KEY(guild_id,user_id,league));
        """)
        player_columns={row[1] for row in con.execute("PRAGMA table_info(players)")}
        if "assists" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN assists INTEGER NOT NULL DEFAULT 0")
        if "mvp" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN mvp INTEGER NOT NULL DEFAULT 0")
        if "nickname" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN nickname TEXT")
        submission_columns={row[1] for row in con.execute("PRAGMA table_info(result_submissions)")}
        if "analysis_json" not in submission_columns: con.execute("ALTER TABLE result_submissions ADD COLUMN analysis_json TEXT")
        if "elo_a_delta" not in submission_columns: con.execute("ALTER TABLE result_submissions ADD COLUMN elo_a_delta INTEGER")
        if "elo_b_delta" not in submission_columns: con.execute("ALTER TABLE result_submissions ADD COLUMN elo_b_delta INTEGER")
        match_columns={row[1] for row in con.execute("PRAGMA table_info(matches)")}
        if "elo_a_delta" not in match_columns: con.execute("ALTER TABLE matches ADD COLUMN elo_a_delta INTEGER")
        if "elo_b_delta" not in match_columns: con.execute("ALTER TABLE matches ADD COLUMN elo_b_delta INTEGER")

def default_elo_deltas(score_a:int,score_b:int):
    """Return the default signed ELO changes for team A and team B."""
    return (25,-18) if int(score_a)>int(score_b) else (-18,25)

def ensure_player(guild_id:int, user_id:int):
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)", (guild_id,user_id))

def set_game_id(guild_id:int,user_id:int,game_id:str):
    ensure_player(guild_id,user_id)
    with connect() as con:
        con.execute("UPDATE players SET game_id=? WHERE guild_id=? AND user_id=?",(game_id,guild_id,user_id))

def set_points(guild_id:int,user_id:int,points:int):
    ensure_player(guild_id,user_id)
    with connect() as con:
        con.execute("UPDATE players SET points=? WHERE guild_id=? AND user_id=?",(max(0,int(points)),guild_id,user_id))

def set_registration(guild_id:int,user_id:int,nickname:str,game_id:str):
    ensure_player(guild_id,user_id)
    with connect() as con:
        con.execute("UPDATE players SET nickname=?,game_id=? WHERE guild_id=? AND user_id=?",(nickname,game_id,guild_id,user_id))

def game_id_owner(guild_id:int,game_id:str):
    """Return the owner of a Game ID only inside one Discord server."""
    with connect() as con:
        row=con.execute(
            "SELECT guild_id,user_id,nickname,game_id FROM players WHERE guild_id=? AND game_id=? LIMIT 1",
            (guild_id,game_id.strip()),
        ).fetchone()
        return dict(row) if row else None

def set_nickname(guild_id:int,user_id:int,nickname:str):
    ensure_player(guild_id,user_id)
    with connect() as con:
        con.execute("UPDATE players SET nickname=? WHERE guild_id=? AND user_id=?",(nickname.strip(),guild_id,user_id))

def restore_registration(guild_id:int,user_id:int,nickname:str,game_id:str):
    """Restore a profile only inside the current Discord server.

    Profiles from another server never count as registration here: every new
    server starts with its own registration and statistics.
    """
    clean_nickname=nickname.strip()
    clean_game_id=game_id.strip()
    with connect() as con:
        source=con.execute(
            """SELECT * FROM players
               WHERE guild_id=? AND game_id=? AND nickname IS NOT NULL
                 AND lower(trim(nickname))=lower(trim(?))
               ORDER BY games DESC, points DESC
               LIMIT 1""",
            (guild_id,clean_game_id,clean_nickname),
        ).fetchone()
        if not source:
            return None
        source=dict(source)
        columns=("game_id","nickname","games","wins","losses","kills","deaths","assists","mvp","points")
        con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(guild_id,user_id))
        con.execute(
            "UPDATE players SET "+",".join(f"{column}=?" for column in columns)+" WHERE guild_id=? AND user_id=?",
            tuple(source[column] for column in columns)+(guild_id,user_id),
        )
        if source["user_id"]!=user_id:
            con.execute("DELETE FROM players WHERE guild_id=? AND user_id=?",(guild_id,source["user_id"]))
        source["guild_id"]=guild_id
        source["user_id"]=user_id
        return source

def player(guild_id:int,user_id:int):
    ensure_player(guild_id,user_id)
    with connect() as con:
        return dict(con.execute("SELECT * FROM players WHERE guild_id=? AND user_id=?",(guild_id,user_id)).fetchone())

def player_by_game_id(guild_id:int,game_id:str):
    with connect() as con:
        row=con.execute("SELECT * FROM players WHERE guild_id=? AND game_id=?",(guild_id,game_id)).fetchone()
        return dict(row) if row else None

def party_for_user(guild_id:int,user_id:int):
    with connect() as con:
        row=con.execute("SELECT p.* FROM parties p JOIN party_members m ON m.party_id=p.id WHERE m.guild_id=? AND m.user_id=?",(guild_id,user_id)).fetchone()
        if not row: return None
        party=dict(row)
        party["members"]=[int(x[0]) for x in con.execute("SELECT user_id FROM party_members WHERE party_id=? ORDER BY user_id=? DESC,user_id",(party["id"],party["leader_id"]))]
        return party

def create_party(guild_id:int,leader_id:int,league:str):
    if party_for_user(guild_id,leader_id): return None
    with connect() as con:
        cur=con.execute("INSERT INTO parties(guild_id,leader_id,league) VALUES(?,?,?)",(guild_id,leader_id,league))
        party_id=cur.lastrowid
        con.execute("INSERT INTO party_members(guild_id,user_id,party_id) VALUES(?,?,?)",(guild_id,leader_id,party_id))
        return party_id

def add_party_member(guild_id:int,party_id:int,user_id:int,max_size:int=3):
    with connect() as con:
        party=con.execute("SELECT * FROM parties WHERE id=? AND guild_id=?",(party_id,guild_id)).fetchone()
        if not party: return "not_found"
        if con.execute("SELECT 1 FROM party_members WHERE guild_id=? AND user_id=?",(guild_id,user_id)).fetchone(): return "already_in_party"
        if con.execute("SELECT COUNT(*) FROM party_members WHERE party_id=?",(party_id,)).fetchone()[0]>=max_size: return "full"
        con.execute("INSERT INTO party_members(guild_id,user_id,party_id) VALUES(?,?,?)",(guild_id,user_id,party_id))
        return "ok"

def leave_party(guild_id:int,user_id:int):
    party=party_for_user(guild_id,user_id)
    if not party: return "not_in_party"
    with connect() as con:
        con.execute("DELETE FROM party_members WHERE guild_id=? AND user_id=?",(guild_id,user_id))
        left=[int(x[0]) for x in con.execute("SELECT user_id FROM party_members WHERE party_id=?",(party["id"],))]
        if not left:
            con.execute("DELETE FROM parties WHERE id=?",(party["id"],)); return "disbanded"
        if party["leader_id"]==user_id:
            con.execute("UPDATE parties SET leader_id=? WHERE id=?",(left[0],party["id"]))
        return "left"

def leaders(guild_id:int, limit:int=10):
    with connect() as con:
        return [dict(x) for x in con.execute("SELECT * FROM players WHERE guild_id=? ORDER BY points DESC,wins DESC LIMIT ?",(guild_id,limit))]

def league_leaders(guild_id:int,league:str,limit:int=10):
    with connect() as con:
        rows=con.execute("""
            SELECT s.*,p.game_id,p.nickname
            FROM player_league_stats s
            LEFT JOIN players p ON p.guild_id=s.guild_id AND p.user_id=s.user_id
            WHERE s.guild_id=? AND s.league=? AND s.games>0
            ORDER BY s.points DESC,s.wins DESC LIMIT ?
        """,(guild_id,league,limit)).fetchall()
        return [dict(row) for row in rows]

def create_match(guild_id,league,map_name,host_id,team_a,team_b):
    with connect() as con:
        cur=con.execute("INSERT INTO matches(guild_id,league,map,host_id,team_a,team_b) VALUES(?,?,?,?,?,?)",(guild_id,league,map_name,host_id,','.join(map(str,team_a)),','.join(map(str,team_b))))
        return cur.lastrowid

def match(match_id:int):
    with connect() as con:
        row=con.execute("SELECT * FROM matches WHERE id=?",(match_id,)).fetchone()
        return dict(row) if row else None

def set_lobby(match_id:int,url:str):
    with connect() as con:
        con.execute("UPDATE matches SET lobby_url=?,status='playing' WHERE id=?",(url,match_id))

def create_submission(guild_id:int,match_id:int,submitter_id:int,score_a:int,score_b:int,screenshot_url:str,analysis_json:str|None=None,elo_a_delta:int|None=None,elo_b_delta:int|None=None):
    if elo_a_delta is None or elo_b_delta is None:
        elo_a_delta,elo_b_delta=default_elo_deltas(score_a,score_b)
    with connect() as con:
        cur=con.execute("INSERT INTO result_submissions(guild_id,match_id,submitter_id,score_a,score_b,elo_a_delta,elo_b_delta,screenshot_url,analysis_json) VALUES(?,?,?,?,?,?,?,?,?)",(guild_id,match_id,submitter_id,score_a,score_b,int(elo_a_delta),int(elo_b_delta),screenshot_url,analysis_json))
        return cur.lastrowid

def submission(submission_id:int):
    with connect() as con:
        row=con.execute("SELECT * FROM result_submissions WHERE id=?",(submission_id,)).fetchone()
        return dict(row) if row else None

def update_pending_submission_score(submission_id:int,guild_id:int,score_a:int,score_b:int):
    elo_a_delta,elo_b_delta=default_elo_deltas(score_a,score_b)
    with connect() as con:
        row=con.execute("SELECT * FROM result_submissions WHERE id=? AND guild_id=? AND status='pending'",(submission_id,guild_id)).fetchone()
        if not row: return False
        con.execute("UPDATE result_submissions SET score_a=?,score_b=?,elo_a_delta=?,elo_b_delta=? WHERE id=?",(int(score_a),int(score_b),elo_a_delta,elo_b_delta,submission_id))
        return True

def update_pending_submission_elo(submission_id:int,guild_id:int,elo_a_delta:int,elo_b_delta:int):
    with connect() as con:
        row=con.execute("SELECT 1 FROM result_submissions WHERE id=? AND guild_id=? AND status='pending'",(submission_id,guild_id)).fetchone()
        if not row: return False
        con.execute("UPDATE result_submissions SET elo_a_delta=?,elo_b_delta=? WHERE id=?",(int(elo_a_delta),int(elo_b_delta),submission_id))
        return True

def update_pending_submission_player_stats(submission_id:int,guild_id:int,user_id:int,kills:int,assists:int,deaths:int,mvp:int):
    with connect() as con:
        submission_row=con.execute("SELECT * FROM result_submissions WHERE id=? AND guild_id=? AND status='pending'",(submission_id,guild_id)).fetchone()
        if not submission_row: return False
        match_row=con.execute("SELECT * FROM matches WHERE id=? AND guild_id=?",(submission_row['match_id'],guild_id)).fetchone()
        if not match_row: return False
        participants={int(value) for value in (match_row['team_a']+','+match_row['team_b']).split(',') if value}
        if user_id not in participants: return False
        try: analysis=json.loads(submission_row['analysis_json'] or '{}')
        except (TypeError,ValueError,json.JSONDecodeError): analysis={}
        matched=list(analysis.get('matched_stats') or [])
        current=next((item for item in matched if int(item.get('user_id') or 0)==user_id),None)
        if current is None:
            current={"user_id":user_id}
            matched.append(current)
        current.update({"kills":max(0,int(kills)),"assists":max(0,int(assists)),"deaths":max(0,int(deaths)),"mvp":max(0,int(mvp))})
        analysis['matched_stats']=matched
        analysis['recognized_players']=len([item for item in matched if item.get('user_id')])
        con.execute("UPDATE result_submissions SET analysis_json=? WHERE id=?",(json.dumps(analysis,ensure_ascii=False),submission_id))
        return True

def review_submission(submission_id:int,status:str,reviewer_id:int,reason:str|None=None):
    with connect() as con:
        cur=con.execute("UPDATE result_submissions SET status=?,reviewer_id=?,reason=? WHERE id=? AND status='pending'",(status,reviewer_id,reason,submission_id))
        return cur.rowcount == 1

def recent_matches(guild_id:int,limit:int=10):
    with connect() as con:
        return [dict(x) for x in con.execute("SELECT * FROM matches WHERE guild_id=? ORDER BY id DESC LIMIT ?",(guild_id,limit))]

def league_player(guild_id:int,user_id:int,league:str):
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO player_league_stats(guild_id,user_id,league) VALUES(?,?,?)",(guild_id,user_id,league))
        return dict(con.execute("SELECT * FROM player_league_stats WHERE guild_id=? AND user_id=? AND league=?",(guild_id,user_id,league)).fetchone())

def apply_player_stats(guild_id:int,stats:list[dict],league:str|None=None):
    with connect() as con:
        for item in stats:
            user_id=item.get("user_id")
            if not user_id: continue
            kills=max(0,int(item.get("kills",0))); deaths=max(0,int(item.get("deaths",0)))
            assists=max(0,int(item.get("assists",0))); mvp=max(0,int(item.get("mvp",0)))
            con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(guild_id,int(user_id)))
            con.execute("UPDATE players SET kills=kills+?,deaths=deaths+?,assists=assists+?,mvp=mvp+? WHERE guild_id=? AND user_id=?",(kills,deaths,assists,mvp,guild_id,int(user_id)))
            if league:
                con.execute("INSERT OR IGNORE INTO player_league_stats(guild_id,user_id,league) VALUES(?,?,?)",(guild_id,int(user_id),league))
                con.execute("UPDATE player_league_stats SET kills=kills+?,deaths=deaths+?,assists=assists+?,mvp=mvp+? WHERE guild_id=? AND user_id=? AND league=?",(kills,deaths,assists,mvp,guild_id,int(user_id),league))

def reverse_player_stats(guild_id:int,stats:list[dict],league:str|None=None):
    with connect() as con:
        for item in stats:
            user_id=item.get("user_id")
            if not user_id: continue
            kills=max(0,int(item.get("kills",0))); deaths=max(0,int(item.get("deaths",0)))
            assists=max(0,int(item.get("assists",0))); mvp=max(0,int(item.get("mvp",0)))
            con.execute("UPDATE players SET kills=MAX(0,kills-?),deaths=MAX(0,deaths-?),assists=MAX(0,assists-?),mvp=MAX(0,mvp-?) WHERE guild_id=? AND user_id=?",(kills,deaths,assists,mvp,guild_id,int(user_id)))
            if league:
                con.execute("UPDATE player_league_stats SET kills=MAX(0,kills-?),deaths=MAX(0,deaths-?),assists=MAX(0,assists-?),mvp=MAX(0,mvp-?) WHERE guild_id=? AND user_id=? AND league=?",(kills,deaths,assists,mvp,guild_id,int(user_id),league))

def approved_submission_for_match(guild_id:int,match_id:int):
    with connect() as con:
        row=con.execute("SELECT * FROM result_submissions WHERE guild_id=? AND match_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(guild_id,match_id)).fetchone()
        return dict(row) if row else None

def update_approved_player_stats(guild_id:int,match_id:int,user_id:int,kills:int,assists:int,deaths:int,mvp:int,editor_id:int):
    values={"kills":max(0,int(kills)),"assists":max(0,int(assists)),"deaths":max(0,int(deaths)),"mvp":max(0,int(mvp))}
    with connect() as con:
        match_row=con.execute("SELECT * FROM matches WHERE id=? AND guild_id=? AND status='finished'",(match_id,guild_id)).fetchone()
        if not match_row: return None
        participants={int(x) for x in (match_row['team_a']+','+match_row['team_b']).split(',') if x}
        if user_id not in participants: return None
        submission=con.execute("SELECT * FROM result_submissions WHERE guild_id=? AND match_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(guild_id,match_id)).fetchone()
        if submission:
            submission_id=submission['id']
            try: analysis=json.loads(submission['analysis_json'] or '{}')
            except (TypeError,ValueError,json.JSONDecodeError): analysis={}
        else:
            analysis={"model":"manual-admin","matched_stats":[]}
            cur=con.execute("INSERT INTO result_submissions(guild_id,match_id,submitter_id,score_a,score_b,screenshot_url,status,reviewer_id,analysis_json) VALUES(?,?,?,?,?,?,'approved',?,?)",(
                guild_id,match_id,editor_id,int(match_row['score_a'] or 0),int(match_row['score_b'] or 0),"manual://admin-statistics",editor_id,json.dumps(analysis,ensure_ascii=False)))
            submission_id=cur.lastrowid
        matched=list(analysis.get('matched_stats') or [])
        current=next((item for item in matched if int(item.get('user_id') or 0)==user_id),None)
        old={key:max(0,int((current or {}).get(key,0))) for key in values}
        if current is None:
            current={"user_id":user_id}
            matched.append(current)
        current.update(values)
        analysis['matched_stats']=matched
        analysis['recognized_players']=len([item for item in matched if item.get('user_id')])
        deltas={key:values[key]-old[key] for key in values}
        con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(guild_id,user_id))
        con.execute("INSERT OR IGNORE INTO player_league_stats(guild_id,user_id,league) VALUES(?,?,?)",(guild_id,user_id,match_row['league']))
        for table,where,params in (
            ('players','guild_id=? AND user_id=?',(guild_id,user_id)),
            ('player_league_stats','guild_id=? AND user_id=? AND league=?',(guild_id,user_id,match_row['league'])),
        ):
            con.execute(f"UPDATE {table} SET kills=MAX(0,kills+?),assists=MAX(0,assists+?),deaths=MAX(0,deaths+?),mvp=MAX(0,mvp+?) WHERE {where}",(
                deltas['kills'],deltas['assists'],deltas['deaths'],deltas['mvp'],*params))
        con.execute("UPDATE result_submissions SET analysis_json=?,reviewer_id=? WHERE id=?",(json.dumps(analysis,ensure_ascii=False),editor_id,submission_id))
        return {"old":old,"new":values,"league":match_row['league'],"submission_id":submission_id}

def finish_match(match_id:int,score_a:int,score_b:int,elo_a_delta:int|None=None,elo_b_delta:int|None=None):
    if elo_a_delta is None or elo_b_delta is None:
        elo_a_delta,elo_b_delta=default_elo_deltas(score_a,score_b)
    elo_a_delta=int(elo_a_delta); elo_b_delta=int(elo_b_delta)
    with connect() as con:
        m=con.execute("SELECT * FROM matches WHERE id=? AND status!='finished'",(match_id,)).fetchone()
        if not m: return False
        a=[int(x) for x in m['team_a'].split(',')]; b=[int(x) for x in m['team_b'].split(',')]
        won_a=score_a>score_b
        for uid in a+b:
            con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(m['guild_id'],uid))
            con.execute("INSERT OR IGNORE INTO player_league_stats(guild_id,user_id,league) VALUES(?,?,?)",(m['guild_id'],uid,m['league']))
        for uid in a:
            values=(1 if won_a else 0,0 if won_a else 1,elo_a_delta)
            con.execute("UPDATE players SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
            con.execute("UPDATE player_league_stats SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
        for uid in b:
            values=(0 if won_a else 1,1 if won_a else 0,elo_b_delta)
            con.execute("UPDATE players SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
            con.execute("UPDATE player_league_stats SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
        con.execute("UPDATE matches SET score_a=?,score_b=?,elo_a_delta=?,elo_b_delta=?,status='finished' WHERE id=?",(score_a,score_b,elo_a_delta,elo_b_delta,match_id))
        return True

def change_finished_match_result(match_id:int,score_a:int,score_b:int,reason:str|None=None):
    with connect() as con:
        m=con.execute("SELECT * FROM matches WHERE id=? AND status='finished'",(match_id,)).fetchone()
        if not m: return False
        team_a=[int(x) for x in m['team_a'].split(',') if x]
        team_b=[int(x) for x in m['team_b'].split(',') if x]
        old_won_a=int(m['score_a'])>int(m['score_b'])
        new_won_a=int(score_a)>int(score_b)
        old_a_delta=int(m['elo_a_delta']) if m['elo_a_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[0]
        old_b_delta=int(m['elo_b_delta']) if m['elo_b_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[1]
        if old_won_a==new_won_a:
            new_a_delta,new_b_delta=old_a_delta,old_b_delta
        else:
            new_a_delta,new_b_delta=default_elo_deltas(score_a,score_b)
        if old_won_a!=new_won_a:
            for uid in team_a:
                values=(1 if old_won_a else 0,1 if new_won_a else 0,0 if old_won_a else 1,0 if new_won_a else 1,new_a_delta-old_a_delta)
                con.execute("UPDATE players SET wins=MAX(0,wins-?)+?,losses=MAX(0,losses-?)+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
                con.execute("UPDATE player_league_stats SET wins=MAX(0,wins-?)+?,losses=MAX(0,losses-?)+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
            for uid in team_b:
                values=(0 if old_won_a else 1,0 if new_won_a else 1,1 if old_won_a else 0,1 if new_won_a else 0,new_b_delta-old_b_delta)
                con.execute("UPDATE players SET wins=MAX(0,wins-?)+?,losses=MAX(0,losses-?)+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
                con.execute("UPDATE player_league_stats SET wins=MAX(0,wins-?)+?,losses=MAX(0,losses-?)+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
        con.execute("UPDATE matches SET score_a=?,score_b=?,elo_a_delta=?,elo_b_delta=? WHERE id=?",(score_a,score_b,new_a_delta,new_b_delta,match_id))
        con.execute("UPDATE result_submissions SET score_a=?,score_b=?,elo_a_delta=?,elo_b_delta=?,reason=COALESCE(?,reason) WHERE match_id=? AND status='approved'",(score_a,score_b,new_a_delta,new_b_delta,reason,match_id))
        return True

def update_finished_match_elo(match_id:int,guild_id:int,elo_a_delta:int,elo_b_delta:int):
    """Replace the already applied ELO changes for a finished match."""
    elo_a_delta=int(elo_a_delta); elo_b_delta=int(elo_b_delta)
    with connect() as con:
        m=con.execute("SELECT * FROM matches WHERE id=? AND guild_id=? AND status='finished'",(match_id,guild_id)).fetchone()
        if not m: return None
        old_a=int(m['elo_a_delta']) if m['elo_a_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[0]
        old_b=int(m['elo_b_delta']) if m['elo_b_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[1]
        diff_a=elo_a_delta-old_a; diff_b=elo_b_delta-old_b
        team_a=[int(x) for x in m['team_a'].split(',') if x]
        team_b=[int(x) for x in m['team_b'].split(',') if x]
        for uid in team_a:
            con.execute("UPDATE players SET points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(diff_a,guild_id,uid))
            con.execute("UPDATE player_league_stats SET points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(diff_a,guild_id,uid,m['league']))
        for uid in team_b:
            con.execute("UPDATE players SET points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(diff_b,guild_id,uid))
            con.execute("UPDATE player_league_stats SET points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(diff_b,guild_id,uid,m['league']))
        con.execute("UPDATE matches SET elo_a_delta=?,elo_b_delta=? WHERE id=?",(elo_a_delta,elo_b_delta,match_id))
        con.execute("UPDATE result_submissions SET elo_a_delta=?,elo_b_delta=? WHERE guild_id=? AND match_id=? AND status='approved'",(elo_a_delta,elo_b_delta,guild_id,match_id))
        return {"old_a":old_a,"old_b":old_b,"new_a":elo_a_delta,"new_b":elo_b_delta}

def cancel_finished_match(match_id:int,reason:str|None=None):
    with connect() as con:
        m=con.execute("SELECT * FROM matches WHERE id=? AND status='finished'",(match_id,)).fetchone()
        if not m: return False
        team_a=[int(x) for x in m['team_a'].split(',') if x]
        team_b=[int(x) for x in m['team_b'].split(',') if x]
        won_a=int(m['score_a'])>int(m['score_b'])
        elo_a_delta=int(m['elo_a_delta']) if m['elo_a_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[0]
        elo_b_delta=int(m['elo_b_delta']) if m['elo_b_delta'] is not None else default_elo_deltas(m['score_a'],m['score_b'])[1]
        for uid in team_a:
            values=(1 if won_a else 0,0 if won_a else 1,-elo_a_delta)
            con.execute("UPDATE players SET games=MAX(0,games-1),wins=MAX(0,wins-?),losses=MAX(0,losses-?),points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
            con.execute("UPDATE player_league_stats SET games=MAX(0,games-1),wins=MAX(0,wins-?),losses=MAX(0,losses-?),points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
        for uid in team_b:
            values=(0 if won_a else 1,1 if won_a else 0,-elo_b_delta)
            con.execute("UPDATE players SET games=MAX(0,games-1),wins=MAX(0,wins-?),losses=MAX(0,losses-?),points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(*values,m['guild_id'],uid))
            con.execute("UPDATE player_league_stats SET games=MAX(0,games-1),wins=MAX(0,wins-?),losses=MAX(0,losses-?),points=MAX(0,points+?) WHERE guild_id=? AND user_id=? AND league=?",(*values,m['guild_id'],uid,m['league']))
        con.execute("UPDATE matches SET score_a=NULL,score_b=NULL,status='cancelled' WHERE id=?",(match_id,))
        con.execute("UPDATE result_submissions SET status='cancelled',reason=COALESCE(?,reason) WHERE match_id=? AND status='approved'",(reason,match_id))
        return True
