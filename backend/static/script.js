document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    // ↓↓↓ ИЗМЕНЕНИЕ: Получаем новый элемент для лога ↓↓↓
    const statusLog = document.getElementById('status-log');
    const resultSection = document.getElementById('result-section');
    const langSwitcher = document.getElementById('lang-switcher');

    let currentLang = 'en';
    let pollingInterval;
    // ↓↓↓ ИЗМЕНЕНИЕ: Переменная для хранения последнего сообщения ↓↓↓
    let lastStatusMessage = '';

    langSwitcher.addEventListener('click', (e) => {
        e.preventDefault();
        if (e.target.classList.contains('lang-link')) {
            langSwitcher.querySelector('.active').classList.remove('active');
            e.target.classList.add('active');
            currentLang = e.target.dataset.lang;
        }
    });

    checkBtn.addEventListener('click', async () => {
        const videoUrl = urlInput.value.trim();
        if (!videoUrl) {
            alert('Пожалуйста, вставьте ссылку на видео.');
            return;
        }

        clearInterval(pollingInterval);
        resultSection.style.display = 'none';
        // ↓↓↓ ИЗМЕНЕНИЕ: Очищаем лог и сбрасываем последнее сообщение ↓↓↓
        statusLog.innerHTML = ''; 
        lastStatusMessage = '';
        
        statusLog.innerHTML = '<p>Отправка запроса на анализ...</p>'; // Начальное сообщение
        statusSection.style.display = 'block';

        try {
            const analyzeResponse = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: videoUrl, lang: currentLang })
            });

            if (!analyzeResponse.ok) {
                const errData = await analyzeResponse.json();
                throw new Error(errData.error || 'Ошибка при запуске анализа.');
            }

            const data = await analyzeResponse.json();
            pollStatus(data.task_id);

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Ошибка: ${error.message}</p>`;
        }
    });

    function pollStatus(taskId) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('Сервер вернул ошибку при проверке статуса.');
                
                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    statusSection.style.display = 'none';
                    displayResults(data.result);
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusLog.innerHTML += `<p style="color:red;">Произошла ошибка при обработке: ${data.result}</p>`;
                } else {
                    // ↓↓↓ ИЗМЕНЕНИЕ: Логика добавления новых сообщений в лог ↓↓↓
                    const newMessage = data.info && data.info.status_message ? data.info.status_message : null;
                    // Добавляем сообщение, только если оно новое
                    if (newMessage && newMessage !== lastStatusMessage) {
                        statusLog.innerHTML += `<p>${newMessage}</p>`;
                        lastStatusMessage = newMessage;
                        // Автоматически прокручиваем лог вниз
                        statusLog.scrollTop = statusLog.scrollHeight;
                    }
                }
            } catch (error) {
                clearInterval(pollingInterval);
                statusLog.innerHTML += `<p style="color:red;">Критическая ошибка опроса: ${error.message}</p>`;
            }
        }, 3000);
    }
    
    // ... (функция displayResults и остальной код без изменений) ...
    function displayResults(data) {
        // ...
    }
});