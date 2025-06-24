document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    let pollingInterval;
    let lastStatusMessage = '';

    checkBtn.addEventListener('click', async () => {
        const userInput = urlInput.value.trim();
        if (!userInput) {
            alert(window.translations.please_paste_video);
            return;
        }
        if (userInput.length > 10000) {
            alert("Input is too long (limit 10000 characters).");
            return;
        }

        clearInterval(pollingInterval);
        statusLog.innerHTML = '';
        lastStatusMessage = '';

        statusLog.innerHTML = '<p>' + window.translations.sending_request + '</p>';
        statusSection.style.display = 'block';

        try {
            const userLang = document.documentElement.lang;
            const analyzeResponse = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: userInput, lang: userLang })
            });

            if (!analyzeResponse.ok) {
                const errData = await analyzeResponse.json();
                throw new Error(errData.error || window.translations.error_starting);
            }

            const data = await analyzeResponse.json();
            pollStatus(data.task_id, userInput, userLang);

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Error: ${error.message}</p>`;
        }
    });

    function pollStatus(taskId, userInput, userLang) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('Server returned an error when checking status.');

                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    // Для новых результатов backend возвращает result.id
                    const id = data.result && data.result.id;
                    if (id) {
                        window.location.href = `/report/${id}`;
                    } else {
                        // fallback для старых данных: просто перезагрузить
                        window.location.reload();
                    }
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusLog.innerHTML += `<p style="color:red;">${window.translations.processing_error} ${data.result}</p>`;
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
                statusLog.innerHTML += `<p style="color:red;">${window.translations.polling_error} ${error.message}</p>`;
            }
        }, 3000);
    }

    // --- Лента, скролл, языки (оставить без изменений) ---
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
                    if (totalLoadedCount < MAX_ITEMS) {
                        const itemHTML = `
                            <a href="/report/${analysis.id}" class="feed-item-link">
                                <img src="${analysis.thumbnail_url}" alt="" class="feed-thumbnail">
                                <div class="feed-info">
                                    <h3 class="feed-title">${analysis.video_title}</h3>
                                    <p class="feed-stats">
                                        ${analysis.confirmed_credibility}% ${window.translations.credibility} / ${analysis.average_confidence}% ${window.translations.confidence}
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
        if (feedContainer.offsetHeight < window.innerHeight && totalLoadedCount < MAX_ITEMS) {
            await loadMoreAnalyses();
            fillScreen();
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

        document.querySelectorAll('.lang-option').forEach(option => {
            option.addEventListener('click', function(e) {
                e.preventDefault();
                const lang = this.getAttribute('href').split('/').pop();
                document.cookie = "lang=" + lang + "; path=/";
                location.reload();
            });
        });

        window.addEventListener('click', () => {
            if (langSwitcher.classList.contains('open')) {
                langSwitcher.classList.remove('open');
            }
        });
    }

    window.addEventListener('scroll', handleScroll);
    fillScreen();
});
