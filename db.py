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
          status TEXT NOT NULL DEFAULT 'waiting', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS result_submissions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
          match_id INTEGER NOT NULL, submitter_id INTEGER NOT NULL,
          score_a INTEGER NOT NULL, score_b INTEGER NOT NULL,
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
        """)
        player_columns={row[1] for row in con.execute("PRAGMA table_info(players)")}
        if "assists" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN assists INTEGER NOT NULL DEFAULT 0")
        if "mvp" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN mvp INTEGER NOT NULL DEFAULT 0")
        if "nickname" not in player_columns: con.execute("ALTER TABLE players ADD COLUMN nickname TEXT")
        submission_columns={row[1] for row in con.execute("PRAGMA table_info(result_submissions)")}
        if "analysis_json" not in submission_columns: con.execute("ALTER TABLE result_submissions ADD COLUMN analysis_json TEXT")

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

def game_id_owner(game_id:str):
    with connect() as con:
        row=con.execute(
            "SELECT guild_id,user_id,nickname,game_id FROM players WHERE game_id=? LIMIT 1",
            (game_id.strip(),),
        ).fetchone()
        return dict(row) if row else None

def set_nickname(guild_id:int,user_id:int,nickname:str):
    ensure_player(guild_id,user_id)
    with connect() as con:
        con.execute("UPDATE players SET nickname=? WHERE guild_id=? AND user_id=?",(nickname.strip(),guild_id,user_id))

def restore_registration(guild_id:int,user_id:int,nickname:str,game_id:str):
    """Restore an existing DOMINION profile by nickname + Standoff 2 ID.

    The current guild is preferred, but a profile from an older DOMINION guild can
    also be imported. Inside the same guild the profile is moved to the current
    Discord account so it cannot be used twice.
    """
    clean_nickname=nickname.strip()
    clean_game_id=game_id.strip()
    with connect() as con:
        source=con.execute(
            """SELECT * FROM players
               WHERE game_id=? AND nickname IS NOT NULL
                 AND lower(trim(nickname))=lower(trim(?))
               ORDER BY CASE WHEN guild_id=? THEN 0 ELSE 1 END, games DESC, points DESC
               LIMIT 1""",
            (clean_game_id,clean_nickname,guild_id),
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
        if source["guild_id"]==guild_id and source["user_id"]!=user_id:
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

def create_submission(guild_id:int,match_id:int,submitter_id:int,score_a:int,score_b:int,screenshot_url:str,analysis_json:str|None=None):
    with connect() as con:
        cur=con.execute("INSERT INTO result_submissions(guild_id,match_id,submitter_id,score_a,score_b,screenshot_url,analysis_json) VALUES(?,?,?,?,?,?,?)",(guild_id,match_id,submitter_id,score_a,score_b,screenshot_url,analysis_json))
        return cur.lastrowid

def submission(submission_id:int):
    with connect() as con:
        row=con.execute("SELECT * FROM result_submissions WHERE id=?",(submission_id,)).fetchone()
        return dict(row) if row else None

def review_submission(submission_id:int,status:str,reviewer_id:int,reason:str|None=None):
    with connect() as con:
        cur=con.execute("UPDATE result_submissions SET status=?,reviewer_id=?,reason=? WHERE id=? AND status='pending'",(status,reviewer_id,reason,submission_id))
        return cur.rowcount == 1

def recent_matches(guild_id:int,limit:int=10):
    with connect() as con:
        return [dict(x) for x in con.execute("SELECT * FROM matches WHERE guild_id=? ORDER BY id DESC LIMIT ?",(guild_id,limit))]

def apply_player_stats(guild_id:int,stats:list[dict]):
    with connect() as con:
        for item in stats:
            user_id=item.get("user_id")
            if not user_id: continue
            con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(guild_id,int(user_id)))
            con.execute("UPDATE players SET kills=kills+?,deaths=deaths+?,assists=assists+?,mvp=mvp+? WHERE guild_id=? AND user_id=?",(
                max(0,int(item.get("kills",0))),max(0,int(item.get("deaths",0))),max(0,int(item.get("assists",0))),max(0,int(item.get("mvp",0))),guild_id,int(user_id)))

def finish_match(match_id:int,score_a:int,score_b:int):
    with connect() as con:
        m=con.execute("SELECT * FROM matches WHERE id=? AND status!='finished'",(match_id,)).fetchone()
        if not m: return False
        a=[int(x) for x in m['team_a'].split(',')]; b=[int(x) for x in m['team_b'].split(',')]
        won_a=score_a>score_b
        for uid in a+b: con.execute("INSERT OR IGNORE INTO players(guild_id,user_id) VALUES(?,?)",(m['guild_id'],uid))
        for uid in a:
            con.execute("UPDATE players SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(1 if won_a else 0,0 if won_a else 1,25 if won_a else -18,m['guild_id'],uid))
        for uid in b:
            con.execute("UPDATE players SET games=games+1,wins=wins+?,losses=losses+?,points=MAX(0,points+?) WHERE guild_id=? AND user_id=?",(0 if won_a else 1,1 if won_a else 0,-18 if won_a else 25,m['guild_id'],uid))
        con.execute("UPDATE matches SET score_a=?,score_b=?,status='finished' WHERE id=?",(score_a,score_b,match_id))
        return True
