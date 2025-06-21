document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('youtube-url');
    const checkBtn = document.getElementById('factcheck-btn');
    const statusSection = document.getElementById('status-section');
    const statusLog = document.getElementById('status-log');
    const resultSection = document.getElementById('result-section');

    let pollingInterval;
    let lastStatusMessage = '';

    checkBtn.addEventListener('click', async () => {
        const videoUrl = urlInput.value.trim();
        if (!videoUrl) {
            alert('Please insert the link to the video.');
            return;
        }

        clearInterval(pollingInterval);
        resultSection.style.display = 'none';
        statusLog.innerHTML = ''; 
        lastStatusMessage = '';
        
        statusLog.innerHTML = '<p>Submitting a request for analysis...</p>';
        statusSection.style.display = 'block';

        try {
            // Определяем язык браузера пользователя и берем основную часть (напр. "en" из "en-US")
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
            pollStatus(data.task_id);

        } catch (error) {
            statusLog.innerHTML += `<p style="color:red;">Error: ${error.message}</p>`;
        }
    });

    function pollStatus(taskId) {
        pollingInterval = setInterval(async () => {
            try {
                const statusResponse = await fetch(`/api/status/${taskId}`);
                if (!statusResponse.ok) throw new Error('The server returned an error while checking its status.');
                
                const data = await statusResponse.json();

                if (data.status === 'SUCCESS') {
                    clearInterval(pollingInterval);
                    statusSection.style.display = 'none';
                    displayResults(data.result);
                } else if (data.status === 'FAILURE') {
                    clearInterval(pollingInterval);
                    statusLog.innerHTML += `<p style="color:red;">An error occurred while processing: ${data.result}</p>`;
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
                statusLog.innerHTML += `<p style="color:red;">Critical poll error: ${error.message}</p>`;
            }
        }, 3000);
    }
    
function displayResults(data) {
    try {
        console.log("--- [DEBUG] displayResults: Function started. Data received.:", data);

        const resultSection = document.getElementById('result-section');
        const progressContainer = document.getElementById('progress-container');
        const confidenceContainer = document.getElementById('confidence-container');
        const reportContainer = document.getElementById('report-container');

        progressContainer.innerHTML = '';
        confidenceContainer.innerHTML = '';
        reportContainer.innerHTML = '';

        if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
            reportContainer.innerHTML = '<p>Error: Incorrect report data format received.</p>';
            resultSection.style.display = 'block';
            return;
        }

        const { verdict_counts, detailed_results, summary_data, average_confidence } = data;

        // --- 1. Отрисовка Прогресс-бара ---
        const totalVerdicts = Object.values(verdict_counts).reduce((a, b) => a + b, 0);
        if (totalVerdicts > 0) {
            const tooltips = {
                'True': 'True statements', 'False': 'False statements',
                'Unverifiable': 'Unverifiable or manipulative statements'
            };
            const segments = [
                { type: 'True', count: verdict_counts['True'] || 0, id: 'true-segment' },
                { type: 'False', count: verdict_counts['False'] || 0, id: 'false-segment' },
                { type: 'Unverifiable', count: verdict_counts['Unverifiable'] || 0, id: 'unverifiable-segment' }
            ];
            segments.forEach(segment_data => {
                if (segment_data.count > 0) {
                    const percentage = (segment_data.count / totalVerdicts) * 100;
                    const segmentDiv = document.createElement('div');
                    segmentDiv.id = segment_data.id;
                    segmentDiv.className = 'progress-segment';
                    segmentDiv.style.width = percentage + '%';
                    segmentDiv.textContent = segment_data.count;
                    segmentDiv.title = tooltips[segment_data.type];
                    progressContainer.appendChild(segmentDiv);
                }
            });
        }

        // --- 2. Вывод средней уверенности ---
        if (average_confidence !== undefined) {
            confidenceContainer.textContent = `Average confidence: ${average_confidence}%`;
        }

        // --- 3. Отрисовка Текстового Отчета ---
        let reportHTML = `
            <div id="report-summary">
                <h2>${summary_data.overall_verdict || ''}</h2>
                <p>${summary_data.overall_assessment || ''}</p>
                <ul>
                    ${(summary_data.key_points || []).map(point => `<li>${point}</li>`).join('')}
                </ul>
            </div>
            <button id="details-toggle" class="details-toggle">Show detailed analysis</button>
            <div id="claim-list-container" style="display: none;">
                <div class="claim-list">
        `;

        // --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
        // Используем правильное имя переменной: detailed_results
        detailed_results.forEach(claim => {
            const verdictClass = (claim.verdict || 'No-data').replace(/[\s/]+/g, '-');
            reportHTML += `<div class="claim-item"><p class="claim-text">${claim.claim || 'Claim text missing'}<span class="claim-verdict verdict-${verdictClass}">${claim.verdict || 'No verdict'}</span></p><div class="claim-explanation"><p>${claim.explanation || ''}</p></div>`;
            if (claim.sources && claim.sources.length > 0) {
                reportHTML += `<div class="claim-sources"><strong>Sources:</strong> `;
                claim.sources.forEach((source, index) => {
                    reportHTML += `<a href="${source}" target="_blank">[${index + 1}]</a> `;
                });
                reportHTML += `</div>`;
            }
            reportHTML += `</div>`;
        });

        reportHTML += `</div></div>`;
        reportContainer.innerHTML = reportHTML;
        
        const toggleButton = document.getElementById('details-toggle');
        const detailsContainer = document.getElementById('claim-list-container');
        if (toggleButton) {
            toggleButton.addEventListener('click', () => {
                const isHidden = detailsContainer.style.display === 'none';
                detailsContainer.style.display = isHidden ? 'block' : 'none';
                toggleButton.textContent = isHidden ? 'Hide detailed analysis' : 'Show detailed analysis';
            });
        }

        resultSection.style.display = 'block';
        
    } catch (e) {
        console.error("!!! [DEBUG] CRITICAL ERROR INSIDE displayResults:", e);
    }
}
});