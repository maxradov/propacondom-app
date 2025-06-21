document.addEventListener('DOMContentLoaded', () => {
    // The `reportData` is passed from the report.html template
    if (typeof reportData !== 'undefined') {
        displayResults(reportData);
    }

    // The listener for the share button will find it even in its new location
    const shareBtn = document.getElementById('share-btn');
    if(shareBtn) {
        shareBtn.addEventListener('click', shareResults);
    }
});

function displayResults(data) {
    const progressContainer = document.getElementById('progress-container');
    const confidenceContainer = document.getElementById('confidence-container');
    const reportContainer = document.getElementById('report-container');

    if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
        reportContainer.innerHTML = '<p>Error: Received incorrect report data format.</p>';
        return;
    }

    const { verdict_counts, detailed_results, summary_data, average_confidence, confirmed_credibility } = data;

    // Render Progress Bar
    const totalVerdicts = Object.values(verdict_counts).reduce((a, b) => a + b, 0);
    if (totalVerdicts > 0) {
        // ... (код отрисовки прогресс-бара остается без изменений) ...
    }

    // Display Stats
    const stats = [];
    if (average_confidence !== undefined) {
        stats.push(`Average confidence: ${average_confidence}%`);
    }
    if (confirmed_credibility !== undefined) {
        stats.push(`Confirmed credibility: ${confirmed_credibility}%`);
    }
    confidenceContainer.innerHTML = stats.join(' <span class="stats-separator">|</span> ');


    // Render the Text Report
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
        // ... (код генерации списка утверждений остается без изменений) ...
    });
    reportHTML += `</div></div>`;
    reportContainer.innerHTML = reportHTML;
    
    // --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    const toggleButton = document.getElementById('details-toggle');
    const detailsContainer = document.getElementById('claim-list-container');
    if (toggleButton && detailsContainer) { // Добавили проверку на detailsContainer
        toggleButton.addEventListener('click', () => {
            const isHidden = detailsContainer.style.display === 'none';
            detailsContainer.style.display = isHidden ? 'block' : 'none';
            // Заменяем текст на английский
            toggleButton.textContent = isHidden ? 'Hide Detailed Analysis' : 'Show Detailed Analysis';
        });
    }
}

function shareResults() {
    // ... (код без изменений) ...
}