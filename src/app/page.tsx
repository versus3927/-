import React from 'react';

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-900 text-white flex flex-col items-center justify-center p-8 font-sans">
      <div className="max-w-2xl w-full bg-slate-800 rounded-2xl shadow-2xl p-8 border border-slate-700 text-center">
        <h1 className="text-3xl font-bold mb-4 text-blue-400">
          Авторег Standoff 2 — Обновлённая версия
        </h1>
        <p className="text-slate-300 mb-6 leading-relaxed">
          Чистый архив бота с новой командой <code className="bg-slate-700 px-2 py-0.5 rounded text-blue-300">апи состояние</code> для проверки баланса и остатка токенов на каждом API-ключе.
        </p>

        <div className="space-y-4 mb-6 text-left bg-slate-900/50 p-6 rounded-xl border border-slate-700">
          <h2 className="text-xl font-semibold text-green-400">🆕 Что добавлено:</h2>
          <ul className="list-disc list-inside text-slate-400 space-y-2">
            <li><code className="text-green-300">апи состояние</code> — показывает статус каждого API-ключа (основного и резервного): активен / исчерпан / на отдыхе, остаток токенов, лимит, использовано</li>
            <li>Алиасы: <code className="text-green-300">апи статус</code>, <code className="text-green-300">api status</code></li>
          </ul>
        </div>

        <div className="space-y-4 mb-6 text-left bg-slate-900/50 p-6 rounded-xl border border-slate-700">
          <h2 className="text-xl font-semibold text-blue-300">📦 Состав архива:</h2>
          <ul className="list-disc list-inside text-slate-400 space-y-1">
            <li><span className="text-slate-200 font-mono">bot.py</span> — основная логика v65 + новая команда</li>
            <li><span className="text-slate-200 font-mono">requirements.txt</span> — зависимости</li>
            <li><span className="text-slate-200 font-mono">Dockerfile / Procfile / railway.json</span> — конфиги Railway</li>
            <li><span className="text-slate-200 font-mono">.gitignore / .env.example</span> — безопасность</li>
            <li><span className="text-slate-200 font-mono">README.md / FIX_NOTES.md</span> — документация</li>
          </ul>
        </div>

        <div className="mb-8 text-left bg-slate-900/50 p-6 rounded-xl border border-emerald-800">
          <h2 className="text-lg font-semibold text-emerald-400 mb-3">💬 Пример ответа команды:</h2>
          <pre className="text-sm text-slate-300 whitespace-pre-wrap font-mono bg-slate-950 p-4 rounded-lg">
{`📊 Состояние API-ключей

🟢 Основной ключ #1 (sk-star-...ec7) — активен
  осталось: 48200 · использовано: 1800 · лимит: 50000

🟢 Основной ключ #2 (sk-star-...730) — активен
  осталось: 49500 · использовано: 500 · лимит: 50000

Итого: 2 ключей · 2 активных · 0 исчерпанных · 0 на отдыхе`}
          </pre>
        </div>

        <a
          href="/bot_standoff_clean.zip"
          download
          className="inline-flex items-center gap-3 bg-blue-600 hover:bg-blue-500 text-white font-bold py-4 px-8 rounded-full transition-all transform hover:scale-105 active:scale-95 shadow-lg"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v12m0 0l-4-4m4 4l4-4m-4 6v0" />
          </svg>
          Скачать обновлённый бот
        </a>

        <p className="mt-6 text-xs text-slate-500 italic">
          Замените bot.py в вашем GitHub-репозитории — Railway автоматически задеплоит обновление.
        </p>
      </div>
    </main>
  );
}
