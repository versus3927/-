import React from 'react';

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-900 text-white flex flex-col items-center justify-center p-8 font-sans">
      <div className="max-w-2xl w-full bg-slate-800 rounded-2xl shadow-2xl p-8 border border-slate-700 text-center">
        <h1 className="text-3xl font-bold mb-4 text-blue-400">
          Чистая версия Авторега Standoff 2
        </h1>
        <p className="text-slate-300 mb-8 leading-relaxed">
          Я проанализировал ваш репозиторий и собрал в этот архив только те файлы, которые нужны для работы бота. 
          Лишние файлы (база данных, генераторы карточек лиги и кэш) были удалены.
        </p>

        <div className="space-y-4 mb-8 text-left bg-slate-900/50 p-6 rounded-xl border border-slate-700">
          <h2 className="text-xl font-semibold text-blue-300">Состав архива:</h2>
          <ul className="list-disc list-inside text-slate-400 space-y-1">
            <li><span className="text-slate-200 font-mono">bot.py</span> — основная логика (v65)</li>
            <li><span className="text-slate-200 font-mono">requirements.txt</span> — зависимости</li>
            <li><span className="text-slate-200 font-mono">Dockerfile / Procfile / railway.json</span> — конфиги для Railway</li>
            <li><span className="text-slate-200 font-mono">.gitignore / .env.example</span> — для чистоты и удобной настройки</li>
            <li><span className="text-slate-200 font-mono">README.md / FIX_NOTES.md</span> — документация и правки</li>
          </ul>
        </div>

        <a 
          href="/bot_standoff_clean.zip" 
          download 
          className="inline-flex items-center gap-3 bg-blue-600 hover:bg-blue-500 text-white font-bold py-4 px-8 rounded-full transition-all transform hover:scale-105 active:scale-95 shadow-lg"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="Status: 4 12v6m0 0l-4-4m4 4l4-4m-4-2V4" />
          </svg>
          Скачать Clean Bot ZIP
        </a>

        <p className="mt-8 text-xs text-slate-500 italic">
          После скачивания загрузите содержимое в новый репозиторий GitHub и подключите к Railway.
        </p>
      </div>
    </main>
  );
}
