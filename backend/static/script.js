document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    
    let pollingInterval;
    let lastStatusMessage = '';

    // Простая функция для извлечения ID видео из URL
    function getVideoId(url) {
        const regex = /(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})/;
        const match = url.match(regex);
        return match ? match[1] : null;
    }

    checkBtn.addEventListener('click', async () => {
        const videoUrl = urlInput.value.trim();
        if (!videoUrl) {
            alert('Пожалуйста, вставьте ссылку на видео.');
            return;
        }

        clearInterval(pollingInterval);
        statusLog.innerHTML = ''; 
        lastStatusMessage = '';
        
        statusLog.innerHTML = '<p>Отправка запроса на анализ...</p>';
        statusSection.style.display = 'block';

        try {
            const userLang = (navigator.language || navigator.userLanguage).split('-')[0];
            const analyzeResponse = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: videoUrl, lang: userLang })
            });

            if (!analyzeResponse.ok) {
                const errData = await analyzeResponse.json();
                throw new Error(errData.error || 'Ошибка при запуске анализа.');
            }

            const data = await analyzeResponse.json();
            // Передаем URL и язык для построения ID отчета
            pollStatus(data.task_id, videoUrl, userLang);

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Ошибка: ${error.message}</p>`;
        }
    });

    function pollStatus(taskId, videoUrl, userLang) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('Сервер вернул ошибку при проверке статуса.');
                
                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    // ПЕРЕНАПРАВЛЕНИЕ на страницу отчета
                    const videoId = getVideoId(videoUrl);
                    if (videoId) {
                        window.location.href = `/report/${videoId}_${userLang}`;
                    } else {
                         throw new Error("Не удалось извлечь ID видео для перенаправления.");
                    }
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusLog.innerHTML += `<p style="color:red;">Произошла ошибка при обработке: ${data.result}</p>`;
                } else {
                    const newMessage = data.info && data.info.status_message ? data.info.status_message : null;
                    if (newMessage && newMessage !== lastStatusMessage) {
                        statusLog.innerHTML += `<p>${newMessage}</p>`;
                        lastStatusMessage = newMessage;
                        statusLog.scrollTop = statusLog.scrollHeight;
                    }
                }
            } catch (error) {
                clearInterval(pollingInterval);
                statusLog.innerHTML += `<p style="color:red;">Критическая ошибка опроса: ${error.message}</p>`;
            }
        }, 3000);
    }
});