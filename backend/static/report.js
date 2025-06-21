document.addEventListener('DOMContentLoaded', () => {
    // Этот код выполняется, когда HTML-страница полностью загружена
    if (typeof reportData !== 'undefined') {
        // Если данные отчета существуют, вызываем функцию для их отображения
        displayResults(reportData);
    }

    // Ищем кнопку "Share" и назначаем ей обработчик клика
    const shareBtn = document.getElementById('share-btn');
    if (shareBtn) {
        shareBtn.addEventListener('click', shareResults);
    }
});

function displayResults(data) {
    // Находим все необходимые контейнеры на странице
    const reportWrapper = document.querySelector('.report-wrapper');
    if (!reportWrapper) { return; } // Выходим, если нет главного контейнера

    const progressContainer = reportWrapper.querySelector('#progress-container');
    const confidenceContainer = reportWrapper.querySelector('#confidence-container');
    const reportContainer = reportWrapper.querySelector('#report-container');

    if (!progressContainer || !confidenceContainer || !reportContainer) {
        console.error('Error: One or more report containers are missing.');
        return;
    }
    if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
        reportContainer.innerHTML = '<p>Error: Received incorrect report data format.</p>';
        return;
    }

    const { verdict_counts, detailed_results, summary_data, average_confidence, confirmed_credibility } = data;
    const totalVerdicts = Object.values(verdict_counts).reduce((a, b) => a + b, 0);
    progressContainer.innerHTML = '';
    
    // --- ВОССТАНОВЛЕНА ЛОГИКА ПРОГРЕСС-БАРА С ПОДСКАЗКАМИ ---
    if (totalVerdicts > 0) {
        // Определяем тексты подсказок
        const tooltips = {
            'True': 'True statements',
            'False': 'False statements',
            'Misleading': 'Misleading or Partly True statements',
            'Partly True': 'Misleading or Partly True statements',
            'Unverifiable': 'Unverifiable statements'
        };
        const segments = [
            { type: 'True', count: verdict_counts['True'] || 0, id: 'true-segment' },
            { type: 'False', count: verdict_counts['False'] || 0, id: 'false-segment' },
            { type: 'Misleading', count: verdict_counts['Misleading'] || 0, id: 'misleading-segment' },
            { type: 'Partly True', count: verdict_counts['Partly True'] || 0, id: 'misleading-segment' },
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
                segmentDiv.title = tooltips[segment_data.type]; // <-- ВОТ ЭТА СТРОКА ДОБАВЛЯЕТ ПОДСКАЗКИ
                progressContainer.appendChild(segmentDiv);
            }
        });
    }

    // --- ВОССТАНОВЛЕНА ЛОГИКА ОТОБРАЖЕНИЯ СТАТИСТИКИ ПОД БАРОМ ---
    const stats = [];
    if (average_confidence !== undefined) {
        stats.push(`Average confidence: ${average_confidence}%`);
    }
    if (confirmed_credibility !== undefined) {
        stats.push(`Confirmed credibility: ${confirmed_credibility}%`);
    }
    confidenceContainer.innerHTML = stats.join(' <span class="stats-separator">|</span> ');


    // --- ВОССТАНОВЛЕНА ПОЛНАЯ ЛОГИКА РЕНДЕРИНГА ТЕКСТОВОГО ОТЧЕТА ---
    let reportHTML = `
        <div id="report-summary">
            <h2>${summary_data.overall_verdict || 'No summary verdict'}</h2>
            <p>${summary_data.overall_assessment || 'No summary assessment'}</p>
            <ul>
                ${(summary_data.key_points || []).map(point => `<li>${point}</li>`).join('')}
            </ul>
        </div>
        <button id="details-toggle" class="details-toggle">Show Detailed Analysis</button>
        <div id="claim-list-container" style="display: none;">
            <div class="claim-list">
    `;
    detailed_results.forEach(claim => {
        const verdictClass = (claim.verdict || 'No-data').replace(/[\s/]+/g, '-');
        reportHTML += `
            <div class="claim-item">
                <p class="claim-text">${claim.claim || 'Claim text missing'}
                    <span class="claim-verdict verdict-${verdictClass}">${claim.verdict || 'No verdict'}</span>
                </p>
                <div class="claim-explanation"><p>${claim.explanation || ''}</p></div>
        `;
        if (claim.sources && claim.sources.length > 0) {
            reportHTML += `<div class="claim-sources"><strong>Sources:</strong> `;
            claim.sources.forEach((source, index) => {
                reportHTML += `<a href="${source}" target="_blank" rel="noopener noreferrer">[${index + 1}]</a> `;
            });
            reportHTML += `</div>`;
        }
        reportHTML += `</div>`;
    });
    reportHTML += `</div></div>`;
    reportContainer.innerHTML = reportHTML;
    
    // --- ВОССТАНОВЛЕНА ЛОГИКА КНОПКИ ДЛЯ ДЕТАЛЬНОГО РАЗБОРА ---
    const toggleButton = document.getElementById('details-toggle');
    const detailsContainer = document.getElementById('claim-list-container');
    if (toggleButton && detailsContainer) {
        toggleButton.addEventListener('click', () => {
            const isHidden = detailsContainer.style.display === 'none';
            detailsContainer.style.display = isHidden ? 'block' : 'none';
            toggleButton.textContent = isHidden ? 'Hide Detailed Analysis' : 'Show Detailed Analysis';
        });
    }
}

// --- ВОССТАНОВЛЕНА ФУНКЦИЯ SHARE ---
function shareResults() {
    const shareData = {
        title: 'Fact-Check Report',
        text: `Check out the fact-check report for this video:`,
        url: window.location.href
    };
    const shareBtn = document.getElementById('share-btn');

    if (navigator.share) {
        navigator.share(shareData)
            .then(() => console.log('Successful share'))
            .catch((error) => console.log('Error sharing', error));
    } else {
        navigator.clipboard.writeText(window.location.href).then(() => {
            const originalText = shareBtn.textContent;
            shareBtn.textContent = 'Link Copied!';
            setTimeout(() => {
                shareBtn.textContent = originalText;
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy text: ', err);
            alert('Failed to copy link.');
        });
    }
}