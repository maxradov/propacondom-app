document.addEventListener('DOMContentLoaded', () => {
    // The `reportData` is passed from the report.html template
    if (typeof reportData !== 'undefined') {
        displayResults(reportData);
    }

    const shareBtn = document.getElementById('share-btn');
    if(shareBtn) {
        shareBtn.addEventListener('click', shareResults);
    }
});

function displayResults(data) {
    const resultSection = document.getElementById('result-section');
    const progressContainer = document.getElementById('progress-container');
    const confidenceContainer = document.getElementById('confidence-container');
    const reportContainer = document.getElementById('report-container');

    if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
        reportContainer.innerHTML = '<p>Error: Received incorrect report data format.</p>';
        return;
    }

    // --- ИЗМЕНЕНИЕ ЗДЕСЬ: Получаем новые данные ---
    const { verdict_counts, detailed_results, summary_data, average_confidence, confirmed_credibility } = data;

    // Render the Progress Bar (без изменений)
    const totalVerdicts = Object.values(verdict_counts).reduce((a, b) => a + b, 0);
    if (totalVerdicts > 0) {
        const tooltips = {
            'True': 'True statements', 'False': 'False statements', 'Misleading': 'Misleading statements', 'Partly True': 'Partly true statements',
            'Unverifiable': 'Unverifiable or manipulative statements'
        };
        const segments = [
            { type: 'True', count: verdict_counts['True'] || 0, id: 'true-segment' },
            { type: 'False', count: verdict_counts['False'] || 0, id: 'false-segment' },
            { type: 'Unverifiable', count: verdict_counts['Unverifiable'] || 0, id: 'unverifiable-segment' },
            // Можно добавить и другие сегменты, если нужно
        ];
        progressContainer.innerHTML = ''; // Очищаем контейнер перед отрисовкой
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

    // --- ИЗМЕНЕНИЕ ЗДЕСЬ: Отображение обоих показателей ---
    const stats = [];
    if (average_confidence !== undefined) {
        stats.push(`Average confidence: ${average_confidence}%`);
    }
    if (confirmed_credibility !== undefined) {
        stats.push(`Confirmed credibility: ${confirmed_credibility}%`);
    }
    confidenceContainer.innerHTML = stats.join(' <span class="stats-separator">|</span> ');


    // Render the Text Report (без изменений)
    let reportHTML = `
        <div id="report-summary">
            <h2>${summary_data.overall_verdict || ''}</h2>
            <p>${summary_data.overall_assessment || ''}</p>
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
            toggleButton.textContent = isHidden ? 'Hide Detailed Analysis' : 'Show Detailed Analysis';
        });
    }
}

function shareResults() {
    // ... (код без изменений)
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