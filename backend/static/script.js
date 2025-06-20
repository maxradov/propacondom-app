document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusText = document.getElementById('status-text');
    const resultSection = document.getElementById('result-section');
    const langSwitcher = document.getElementById('lang-switcher');
    // const reportContainer = document.getElementById('report-container'); // УБИРАЕМ ЭТУ СТРОКУ

    let currentLang = 'en';
    let pollingInterval;

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
        resultSection.innerHTML = ''; // Очищаем старый отчет
        statusText.textContent = 'Отправка запроса на анализ...';
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
            statusText.textContent = 'Задача в очереди. Начинаем обработку...';
            pollStatus(data.task_id);

        } catch (error) {
            statusText.textContent = `Ошибка: ${error.message}`;
        }
    });

    function pollStatus(taskId) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) {
                    // Используем текст ошибки с сервера, если он есть
                    const errorText = await statusResponse.text();
                    throw new Error(errorText || 'Сервер вернул ошибку при проверке статуса.');
                }
                
                const data = await statusResponse.json();

                // ↓↓↓ ИЗМЕНЕНИЕ: Добавляем обработку нового формата ответа ↓↓↓
                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    statusSection.style.display = 'none';
                    displayResults(data.result);
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusSection.style.display = 'block';
                    statusText.textContent = `Произошла ошибка при обработке: ${data.result}`;
                } else {
                    // Эта ветка теперь обрабатывает PENDING, PROGRESS и другие промежуточные состояния
                    const message = data.info && data.info.status_message 
                        ? data.info.status_message 
                        : 'Анализ в процессе...'; // Сообщение по умолчанию
                    statusText.textContent = message;
                }
            } catch (error) {
                clearInterval(pollingInterval);
                statusText.textContent = `Критическая ошибка опроса: ${error.message}`;
            }
        }, 3000); // Уменьшим интервал до 3 секунд для более плавного обновления
    }

    function displayResults(data) {
        resultSection.innerHTML = '';

        if (data.error) {
            resultSection.innerHTML = `<h2>Ошибка анализа</h2><p>${data.error}</p>`;
            resultSection.style.display = 'block';
            return;
        }
        
        const counts = data.verdict_counts;
        const total = Object.values(counts).reduce((sum, val) => sum + val, 0);

        let progressBarHTML = '';
        if (total > 0) {
            const true_percent = (counts.true / total) * 100;
            const false_percent = (counts.false / total) * 100;
            const manip_percent = (counts.manipulation / total) * 100;
            const nodata_percent = (counts.nodata / total) * 100;

            progressBarHTML = `
                <div class="progress-container">
                    <div class="progress-segment" style="width: ${true_percent}%; background-color: var(--color-true);" title="Правда"><span>${Math.round(true_percent)}%</span></div>
                    <div class="progress-segment" style="width: ${manip_percent}%; background-color: var(--color-manipulation);" title="Манипуляция"><span>${Math.round(manip_percent)}%</span></div>
                    <div class="progress-segment" style="width: ${false_percent}%; background-color: var(--color-false);" title="Ложь"><span>${Math.round(false_percent)}%</span></div>
                    <div class="progress-segment" style="width: ${nodata_percent}%; background-color: var(--color-nodata);" title="Нет данных"><span>${Math.round(nodata_percent)}%</span></div>
                </div>`;
        }

        const summaryHTML = `<div id="report-summary">${data.summary_html.replace(/\n/g, '<br>')}</div>`;

        let detailsHTML = '<div class="claim-list" style="display:none;">';
        data.detailed_results.forEach(item => {
            const verdictClass = (item.verdict || "No-data").replace(/[\s/]/g, '-');
            detailsHTML += `
                <div class="claim-item">
                    <p class="claim-text">${item.claim} <span class="claim-verdict verdict-${verdictClass}">${item.verdict}</span></p>
                    <p class="claim-explanation">${item.explanation}</p>
                    ${item.sources && item.sources.length > 0 ? `<div class="claim-sources"><strong>Источники:</strong><br/>${item.sources.map(s => `<a href="${s}" target="_blank" rel="noopener noreferrer">${s}</a>`).join('<br/>')}</div>` : ''}
                </div>`;
        });
        detailsHTML += '</div>';

        const toggleButtonHTML = data.detailed_results.length > 0 ? `<button id="toggle-details" class="details-toggle">Показать подробный разбор</button>` : '';

        const finalHTML = `
            <h2>Итоги анализа</h2>
            ${progressBarHTML}
            <div id="report-container">
                ${summaryHTML}
                ${toggleButtonHTML}
                ${detailsHTML}
            </div>`;
        resultSection.innerHTML = finalHTML;
        resultSection.style.display = 'block';
        
        const toggleBtn = document.getElementById('toggle-details');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const detailsList = document.querySelector('.claim-list');
                const isHidden = detailsList.style.display === 'none';
                detailsList.style.display = isHidden ? 'block' : 'none';
                toggleBtn.textContent = isHidden ? 'Скрыть подробный разбор' : 'Показать подробный разбор';
            });
        }
    }
});