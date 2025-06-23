document.addEventListener('DOMContentLoaded', () => {
    // --- Секция для формы анализа (без изменений) ---
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    let pollingInterval;
    let lastStatusMessage = '';

    // Simple function to extract video ID from URL
    function getVideoId(url) {
        const regex = /(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/|\/live\/|googleusercontent\.com\/youtube\.com\/)([a-zA-Z0-9_-]{11})/;
        const match = url.match(regex);
        return match ? match[1] : null;
    }

    checkBtn.addEventListener('click', async () => {
        const videoUrl = urlInput.value.trim();
        if (!videoUrl) {
            alert('Please paste a video link.');
            return;
        }
		
		const videoIdForCheck = getVideoId(videoUrl);
        if (!videoIdForCheck) {
            alert('Please enter a valid YouTube video link!');
            return; // Останавливаем выполнение, если ссылка некорректна
        }

        clearInterval(pollingInterval);
        statusLog.innerHTML = ''; 
        lastStatusMessage = '';
        
        statusLog.innerHTML = '<p>Sending request for analysis...</p>';
        statusSection.style.display = 'block';

        try {
            const userLang = document.documentElement.lang;
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

    // --- НОВАЯ ЛОГИКА ДЛЯ ДИНАМИЧЕСКОЙ ЛЕНТЫ ---
    const feedContainer = document.getElementById('feed-container');
    const loader = document.getElementById('feed-loader');
    if (!feedContainer) return; // Если ленты нет на странице, ничего не делаем

    let isLoading = false;
    let totalLoadedCount = feedContainer.children.length; // Считаем, сколько уже загружено сервером
    const MAX_ITEMS = 50; // Максимальное количество видео

    // Функция для загрузки следующей порции видео
    const loadMoreAnalyses = async () => {
        if (isLoading || totalLoadedCount >= MAX_ITEMS) return;

        const lastItem = feedContainer.querySelector('.feed-item:last-child');
        if (!lastItem) { // На случай, если лента изначально пуста
             if(loader) loader.style.display = 'none';
             return;
        }

        isLoading = true;
        if (loader) loader.style.display = 'block';

        const lastTimestamp = lastItem.dataset.timestamp;

        try {
            const response = await fetch(`/api/get_recent_analyses?last_timestamp=${encodeURIComponent(lastTimestamp)}`);
            if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);
            const newAnalyses = await response.json();

            if (newAnalyses.length > 0) {
                newAnalyses.forEach(analysis => {
                    // Проверяем, не превысим ли мы лимит
                    if (totalLoadedCount < MAX_ITEMS) {
                        const itemHTML = `
                            <a href="/report/${analysis.id}" class="feed-item-link">
                                <img src="${analysis.thumbnail_url}" alt="" class="feed-thumbnail">
                                <div class="feed-info">
                                    <h3 class="feed-title">${analysis.video_title}</h3>
                                    <p class="feed-stats">
                                        ${analysis.confirmed_credibility}% Credibility / ${analysis.average_confidence}% Confidence
                                    </p>
                                </div>
                            </a>`;
                        const newItem = document.createElement('div');
                        newItem.className = 'feed-item';
                        newItem.dataset.timestamp = analysis.created_at;
                        newItem.innerHTML = itemHTML;
                        feedContainer.appendChild(newItem);
                        totalLoadedCount++; // Увеличиваем счетчик
                    }
                });
            }
            
            // Если достигли лимита или больше нет данных
            if (newAnalyses.length === 0 || totalLoadedCount >= MAX_ITEMS) {
                window.removeEventListener('scroll', handleScroll);
                if (loader) loader.textContent = 'No more results';
            }

        } catch (error) {
            console.error('Failed to load more analyses:', error);
            if (loader) loader.textContent = 'Failed to load';
        }

        isLoading = false;
        if (loader && loader.textContent === 'Loading more...') {
            loader.style.display = 'none';
        }
    };

    // Функция для проверки, нужно ли догружать видео, чтобы заполнить экран
    const fillScreen = async () => {
        // Выполняем, только если есть скроллбар (т.е. контент не помещается)
        // или если контент помещается, но его мало
        if (feedContainer.offsetHeight < window.innerHeight && totalLoadedCount < MAX_ITEMS) {
            await loadMoreAnalyses();
            // Рекурсивно вызываем снова, пока экран не заполнится или не достигнем лимита
            fillScreen(); 
        }
    };

    // Обработчик скролла
    const handleScroll = () => {
        if (window.innerHeight + window.scrollY >= document.documentElement.offsetHeight - 200) {
            loadMoreAnalyses();
        }
    };
	
	// --- ЛОГИКА ДЛЯ ПЕРЕКЛЮЧАТЕЛЯ ЯЗЫКОВ ---
    const langSwitcher = document.querySelector('.lang-switcher');
    if (langSwitcher) {
        const currentLang = langSwitcher.querySelector('.lang-switcher-current');
        
        currentLang.addEventListener('click', (event) => {
            event.stopPropagation(); // Предотвращаем закрытие по клику на самом элементе
            langSwitcher.classList.toggle('open');
        });

        // Закрываем дропдаун, если клик был вне его области
        window.addEventListener('click', () => {
            if (langSwitcher.classList.contains('open')) {
                langSwitcher.classList.remove('open');
            }
        });
    }

    window.addEventListener('scroll', handleScroll);
    fillScreen(); // <-- Запускаем начальное заполнение экрана
});