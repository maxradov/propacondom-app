// script.js

document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    const claimSelectionContainer = document.createElement('div');
    claimSelectionContainer.id = 'claim-selection-container';
    statusSection.parentNode.insertBefore(claimSelectionContainer, statusSection.nextSibling);

    let pollingInterval;
    let lastStatusMessage = '';
    const MAX_CLAIMS_TO_CHECK = 5; // Константа для ограничения выбора

    checkBtn.addEventListener('click', async () => {
        const userInput = urlInput.value.trim();
        if (!userInput) {
            alert(window.translations.please_paste_video || "Please provide a URL or text.");
            return;
        }

        clearInterval(pollingInterval);
        statusLog.innerHTML = '';
        lastStatusMessage = '';
        claimSelectionContainer.innerHTML = '';
        statusLog.innerHTML = `<p>${window.translations.sending_request || 'Sending request...'}</p>`;
        statusSection.style.display = 'block';

        try {
            const userLang = document.documentElement.lang || 'en';
            const analyzeResponse = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: userInput, lang: userLang })
            });

            if (!analyzeResponse.ok) {
                const errData = await analyzeResponse.json();
                throw new Error(errData.error || errData.result || (window.translations.error_starting || 'Error starting analysis.'));
            }
            
            // ИСПРАВЛЕНО: Добавлено получение JSON из ответа
            const data = await analyzeResponse.json();
            if (data.task_id) {
                pollStatus(data.task_id, 'extract_claims'); // Запускаем отслеживание задачи №1
            } else {
                 // После успешного анализа всегда редиректим на страницу репорта!
				window.location.href = '/report/' + data.id;
            }

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Error: ${error.message}</p>`;
            statusSection.style.display = 'block'; // Убедимся, что секция видна
        }
    });

    // ИСПРАВЛЕНО: Параметры функции теперь соответствуют её использованию
    function pollStatus(taskId, currentStage) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('Server returned an error when checking status.');

                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    if (currentStage === 'extract_claims') {
                        // --- Этап 1 УСПЕХ: Утверждения извлечены ---
                        // После извлечения клеймов — редирект на страницу выбора клеймов/репорта:
						if (data.result && data.result.id) {
							window.location.href = '/report/' + data.result.id;
						}
                    } else if (currentStage === 'fact_check_selected') {
                        // --- Этап 2 УСПЕХ: Проверка фактов завершена ---
                        const analysisId = data.result.id;
                        window.location.href = `/report/${analysisId}`;
                    }
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    const errorMessage = data.result || (window.translations.processing_error || "An error occurred during processing.");
                    statusLog.innerHTML += `<p style="color:red;">${errorMessage}</p>`;
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
                statusLog.innerHTML += `<p style="color:red;">${(window.translations.polling_error || 'Polling error:')} ${error.message}</p>`;
            }
        }, 3000);
    }

    // ПЕРЕПИСАНО: Функция полностью переработана для нового UX
    function renderClaimSelectionUI(analysisData) {
        const analysisId = analysisData.id;
        const claims = analysisData.claims_for_selection;
		// Новый блок: получаем метаданные
		const videoTitle = analysisData.video_title || "";
		const thumbnailUrl = analysisData.thumbnail_url || "";
		const sourceUrl = analysisData.source_url || "";

		let metaHTML = "";
		if (videoTitle || thumbnailUrl) {
			metaHTML += `<div class="claim-selection-meta" style="display: flex; align-items: flex-start; gap: 1.5rem; margin-bottom: 2rem;">`;
			if (thumbnailUrl) {
				metaHTML += `<a href="${sourceUrl}" target="_blank" rel="noopener">
					<img src="${thumbnailUrl}" alt="Video thumbnail" class="video-thumbnail" style="width: 220px; border-radius: 8px; border: 1px solid #dee2e6;">
				</a>`;
			}
			metaHTML += `<div style="flex: 1;">
				<a href="${sourceUrl}" target="_blank" rel="noopener" style="font-weight: bold; font-size: 1.25rem; text-decoration: none; color: #007bff; word-break: break-word;">
					${videoTitle}
				</a>
			</div>`;
			metaHTML += `</div>`;
		}
        if (!claims || claims.length === 0) {
             claimSelectionContainer.innerHTML = `<p>${window.translations.no_claims_found || 'Could not extract any claims to check.'}</p>`;
             return;
        }

        let claimsHTML = `
            <h3>${(window.translations.select_claims_title || 'Select up to 5 claims to verify')}</h3>
            <div class="claims-list">`;
        
        claims.forEach((claimData, index) => {
			const { hash, text, is_cached, cached_data } = claimData;
			const sanitizedText = text.replace(/"/g, '&quot;'); // Защита от кавычек

			if (is_cached) {
				let verdictText = '';
				if (cached_data && cached_data.verdict) {
					verdictText = `${window.translations.already_checked || 'Already checked'}: ${cached_data.verdict}`;
				} else {
					verdictText = window.translations.already_checked || 'Already checked';
				}
				claimsHTML += `
					<div class="claim-checkbox-item cached">
						<input type="checkbox" id="claim-${index}" value="${hash}" checked disabled>
						<label for="claim-${index}">${text} <span>(${verdictText})</span></label>
					</div>`;
			} else {
				claimsHTML += `
					<div class="claim-checkbox-item">
						<input type="checkbox" id="claim-${index}" name="claim-to-check" value="${hash}" data-text="${sanitizedText}">
						<label for="claim-${index}">${text}</label>
					</div>`;
			}
		});

        claimsHTML += `</div><button id="run-selected-factcheck-btn" disabled>${(window.translations.fact_check_selected_button || 'Fact-Check Selected')}</button>`;
        
        claimSelectionContainer.innerHTML = metaHTML + claimsHTML;

        const checkBoxes = claimSelectionContainer.querySelectorAll('input[name="claim-to-check"]');
        const runCheckBtn = document.getElementById('run-selected-factcheck-btn');

        checkBoxes.forEach(box => {
            box.addEventListener('change', (e) => {
                const selected = Array.from(checkBoxes).filter(i => i.checked);
                
                if (selected.length > MAX_CLAIMS_TO_CHECK) {
                    e.preventDefault();
                    box.checked = false;
                    alert(`${(window.translations.limit_selection_alert || 'You can only select up to')} ${MAX_CLAIMS_TO_CHECK} ${(window.translations.claims_alert || 'claims')}.`);
                }
                
                runCheckBtn.disabled = selected.length === 0;
            });
        });

        runCheckBtn.addEventListener('click', () => {
            // ИСПРАВЛЕНО: Собираем массив объектов для отправки на бэкенд
            const selectedClaimsData = Array.from(checkBoxes)
                .filter(i => i.checked)
                .map(i => ({ hash: i.value, text: i.dataset.text }));
            startSelectedFactCheck(analysisId, selectedClaimsData);
        });
    }

    async function startSelectedFactCheck(analysisId, claimsData) {
        claimSelectionContainer.innerHTML = ''; // Очищаем UI выбора
        statusSection.style.display = 'block'; // Показываем статус снова
        lastStatusMessage = '';
        statusLog.innerHTML = `<p>${(window.translations.sending_request_for_checking || 'Sending selected claims for final analysis...')}</p>`;
        
        try {
            const response = await fetch('/api/fact_check_selected', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // ИЗМЕНЕНО: Отправляем массив объектов
                body: JSON.stringify({ analysis_id: analysisId, selected_claims_data: claimsData })
            });
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || errData.result || (window.translations.error_starting || 'Error starting analysis.'));
            }
            const data = await response.json();
            pollStatus(data.task_id, 'fact_check_selected'); // Отслеживаем задачу №2
        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Error: ${error.message}</p>`;
        }
    }


    // --- Лента, скролл, языки (оставлено без изменений) ---
    const feedContainer = document.getElementById('feed-container');
    const loader = document.getElementById('feed-loader');
    if (!feedContainer) return;

    let isLoading = false;
    let totalLoadedCount = feedContainer.children.length;
    const MAX_ITEMS = 50;

    const loadMoreAnalyses = async () => {
        if (isLoading || totalLoadedCount >= MAX_ITEMS) return;

        const lastItem = feedContainer.querySelector('.feed-item:last-child');
        if (!lastItem) {
            if (loader) loader.style.display = 'none';
            return;
        }

        isLoading = true;
        if (loader) loader.textContent = window.translations.loading_more;
        if (loader) loader.style.display = 'block';

        const lastTimestamp = lastItem.dataset.timestamp;

        try {
            const response = await fetch(`/api/get_recent_analyses?last_timestamp=${encodeURIComponent(lastTimestamp)}`);
            if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);

            const newAnalyses = await response.json();
            if (newAnalyses.length > 0) {
                newAnalyses.forEach(analysis => {
                    if (totalLoadedCount < MAX_ITEMS && analysis.video_title !== 'Title Not Found') {
                        let thumbnail = analysis.thumbnail_url;
                        if (analysis.input_type === 'text') thumbnail = "/static/text-placeholder.png";
                        if (analysis.input_type === 'url' && !thumbnail) thumbnail = "/static/url-placeholder.png";
                        if (!thumbnail) thumbnail = "/static/default-placeholder.png";

                        const itemHTML = `
                            <a href="/report/${analysis.id}" class="feed-item-link">
                                <img src="${thumbnail}" alt="Thumbnail" class="feed-thumbnail" onerror="this.src='/static/default-placeholder.png'">
                                <div class="feed-info">
                                    <h3 class="feed-title">${analysis.video_title}</h3>
                                    <p class="feed-stats">
                                        ${analysis.confirmed_credibility}% ${(window.translations.credibility || 'Credibility')} / ${analysis.average_confidence}% ${(window.translations.confidence || 'Confidence')}
                                    </p>
                                </div>
                            </a>`;
                        const newItem = document.createElement('div');
                        newItem.className = 'feed-item';
                        newItem.dataset.timestamp = analysis.created_at;
                        newItem.innerHTML = itemHTML;
                        feedContainer.appendChild(newItem);
                        totalLoadedCount++;
                    }
                });
            }

            if (newAnalyses.length === 0 || totalLoadedCount >= MAX_ITEMS) {
                window.removeEventListener('scroll', handleScroll);
                if (loader) loader.textContent = window.translations.no_more_results;
            }

        } catch (error) {
            console.error('Failed to load more analyses:', error);
            if (loader) loader.textContent = window.translations.failed_to_load;
        }

        isLoading = false;
        if (loader && loader.textContent === window.translations.loading_more) {
            loader.style.display = 'none';
        }
    };

    const fillScreen = async () => {
        if (document.body.scrollHeight <= window.innerHeight && totalLoadedCount < MAX_ITEMS && totalLoadedCount > 0) {
            await loadMoreAnalyses();
            // Recursive call if still not filling screen
            if (document.body.scrollHeight <= window.innerHeight) {
                setTimeout(fillScreen, 500);
            }
        }
    };

    const handleScroll = () => {
        if (window.innerHeight + window.scrollY >= document.documentElement.offsetHeight - 200) {
            loadMoreAnalyses();
        }
    };
    
    // --- Логика языкового переключателя ---
    const langSwitcher = document.querySelector('.lang-switcher');
    if (langSwitcher) {
        const currentLang = langSwitcher.querySelector('.lang-switcher-current');
        currentLang.addEventListener('click', (event) => {
            event.stopPropagation();
            langSwitcher.classList.toggle('open');
        });
        window.addEventListener('click', () => {
            if (langSwitcher.classList.contains('open')) {
                langSwitcher.classList.remove('open');
            }
        });
    }

    window.addEventListener('scroll', handleScroll);
    if(feedContainer.children.length > 0){
        setTimeout(fillScreen, 100);
    }
});