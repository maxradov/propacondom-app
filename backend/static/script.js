document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    
    let pollingInterval;
    let lastStatusMessage = '';

    // Simple function to extract video ID from URL
    function getVideoId(url) {
        const regex = /(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})/;
        const match = url.match(regex);
        return match ? match[1] : null;
    }

    checkBtn.addEventListener('click', async () => {
        const videoUrl = urlInput.value.trim();
        if (!videoUrl) {
            alert('Please paste a video link.');
            return;
        }

        clearInterval(pollingInterval);
        statusLog.innerHTML = ''; 
        lastStatusMessage = '';
        
        statusLog.innerHTML = '<p>Sending request for analysis...</p>';
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
                throw new Error(errData.error || 'Error starting analysis.');
            }

            const data = await analyzeResponse.json();
            // Passing URL and language to build the report ID
            pollStatus(data.task_id, videoUrl, userLang);

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Error: ${error.message}</p>`;
        }
    });

    function pollStatus(taskId, videoUrl, userLang) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('Server returned an error when checking status.');
                
                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    // REDIRECT to the report page
                    const videoId = getVideoId(videoUrl);
                    if (videoId) {
                        window.location.href = `/report/${videoId}_${userLang}`;
                    } else {
                         throw new Error("Could not extract video ID for redirection.");
                    }
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusLog.innerHTML += `<p style="color:red;">An error occurred during processing: ${data.result}</p>`;
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
                statusLog.innerHTML += `<p style="color:red;">Critical polling error: ${error.message}</p>`;
            }
        }, 3000);
    }
});

// Добавьте этот код в конец вашего файла script.js

document.addEventListener('DOMContentLoaded', () => {
    // ... (существующий код для формы остается здесь) ...

    // --- НОВЫЙ КОД ДЛЯ БЕСКОНЕЧНОЙ ПРОКРУТКИ ---
    const feedContainer = document.getElementById('feed-container');
    const loader = document.getElementById('feed-loader');
    let isLoading = false;

	const loadMoreAnalyses = async () => {
		if (isLoading) return;

		const lastItem = feedContainer.querySelector('.feed-item:last-child');
		if (!lastItem) return;

		isLoading = true;
		if(loader) loader.style.display = 'block';

		const lastTimestamp = lastItem.dataset.timestamp;

		try {
			// --- ИЗМЕНЕНИЕ: Кодируем timestamp для безопасной передачи в URL ---
			const response = await fetch(`/api/get_recent_analyses?last_timestamp=${encodeURIComponent(lastTimestamp)}`);
			
			// Добавляем проверку, что ответ действительно успешный
			if (!response.ok) {
				throw new Error(`Server responded with status: ${response.status}`);
			}

			const newAnalyses = await response.json();

			if (newAnalyses.length > 0) {
				newAnalyses.forEach(analysis => {
					const itemHTML = `
						<a href="/report/${analysis.id}" class="feed-item-link">
							<img src="${analysis.thumbnail_url}" alt="" class="feed-thumbnail">
							<div class="feed-info">
								<h3 class="feed-title">${analysis.video_title}</h3>
								<p class="feed-stats">
									${analysis.confirmed_credibility}% Credibility / ${analysis.average_confidence}% Confidence
								</p>
							</div>
						</a>
					`;
					const newItem = document.createElement('div');
					newItem.className = 'feed-item';
					newItem.dataset.timestamp = analysis.created_at;
					newItem.innerHTML = itemHTML;
					feedContainer.appendChild(newItem);
				});
			} else {
				window.removeEventListener('scroll', handleScroll);
				if(loader) loader.textContent = 'No more results';
			}
		} catch (error) {
			console.error('Failed to load more analyses:', error);
			if(loader) loader.textContent = 'Failed to load'; // <-- Теперь вы видите это сообщение
		}

		isLoading = false;
		// Скрываем загрузчик, только если он не показывает финальное сообщение
		if(loader && loader.textContent !== 'No more results' && loader.textContent !== 'Failed to load') {
			loader.style.display = 'none';
		}
	};

    const handleScroll = () => {
        // Проверяем, доскроллил ли пользователь до низа страницы
        if (window.innerHeight + window.scrollY >= document.documentElement.offsetHeight - 100) {
            loadMoreAnalyses();
        }
    };

    window.addEventListener('scroll', handleScroll);
});