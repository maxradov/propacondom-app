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
		const resultSection = document.getElementById('result-section');
		const progressContainer = document.getElementById('progress-container');
		const reportContainer = document.getElementById('report-container');

		// Очищаем предыдущие результаты
		progressContainer.innerHTML = '';
		reportContainer.innerHTML = '';

		// --- ИЗМЕНЕНИЕ ТУТ ---
		// Проверяем наличие ключа detailed_results вместо report
		if (!data || !data.detailed_results) {
			reportContainer.innerHTML = '<p>Ошибка: получен некорректный формат данных отчета.</p>';
			resultSection.style.display = 'block';
			return;
		}

		// --- И ИЗМЕНЕНИЕ ТУТ ---
		// Работаем напрямую с объектом data, а не data.report
		const report = data;
		const verdictCounts = report.verdict_counts || {};
		const detailedResults = report.detailed_results || [];
		const summary_html = report.summary_html || 'Итоговый отчет не был сгенерирован.';

		// --- 1. Отрисовка Прогресс-бара (логика без изменений) ---
		const totalVerdicts = Object.values(verdictCounts).reduce((a, b) => a + b, 0);
		if (totalVerdicts > 0) {
			const segments = {
				'True': { id: 'true-segment', label: 'Правда', count: verdictCounts['True'] || 0 },
				'False': { id: 'false-segment', label: 'Ложь', count: verdictCounts['False'] || 0 },
				'Partly True/Manipulation': { id: 'manipulation-segment', label: 'Манипуляция', count: verdictCounts['Partly True/Manipulation'] || 0 },
			};

			for (const key in segments) {
				const segment_data = segments[key];
				if (segment_data.count > 0) {
					const percentage = (segment_data.count / totalVerdicts) * 100;
					const segmentDiv = document.createElement('div');
					segmentDiv.id = segment_data.id;
					segmentDiv.className = 'progress-segment';
					segmentDiv.style.width = percentage + '%';
					segmentDiv.textContent = `${segment_data.label} (${Math.round(percentage)}%)`;
					progressContainer.appendChild(segmentDiv);
				}
			}
		}

		// --- 2. Отрисовка Отчета (логика без изменений) ---
		let reportHTML = `
			<div id="report-summary">
				${summary_html.replace(/\n/g, '<br>')}
			</div>
			<button id="details-toggle" class="details-toggle">Показать детальный разбор</button>
			<div id="claim-list-container" style="display: none;">
				<div class="claim-list">
		`;

		detailedResults.forEach(claim => {
			const verdictClass = (claim.verdict || 'No-data').replace(/[\s/]+/g, '-');
			reportHTML += `
				<div class="claim-item">
					<p class="claim-text">
						${claim.claim}
						<span class="claim-verdict verdict-${verdictClass}">${claim.verdict}</span>
					</p>
					<div class="claim-explanation">
						<p>${claim.explanation}</p>
					</div>
			`;
			if (claim.sources && claim.sources.length > 0) {
				reportHTML += `<div class="claim-sources"><strong>Источники:</strong> `;
				claim.sources.forEach((source, index) => {
					reportHTML += `<a href="${source}" target="_blank">[${index + 1}]</a> `;
				});
				reportHTML += `</div>`;
			}
			reportHTML += `</div>`;
		});

		reportHTML += `
				</div> 
			</div>
		`;

		reportContainer.innerHTML = reportHTML;

		// --- 3. Добавление логики для кнопки (логика без изменений) ---
		const toggleButton = document.getElementById('details-toggle');
		const detailsContainer = document.getElementById('claim-list-container');
		if (toggleButton) {
			toggleButton.addEventListener('click', () => {
				const isHidden = detailsContainer.style.display === 'none';
				detailsContainer.style.display = isHidden ? 'block' : 'none';
				toggleButton.textContent = isHidden ? 'Скрыть детальный разбор' : 'Показать детальный разбор';
			});
		}

		resultSection.style.display = 'block';
	}
});