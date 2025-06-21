document.addEventListener('DOMContentLoaded', () => {
    if (typeof reportData !== 'undefined') {
        displayResults(reportData);
    }

    const shareBtn = document.getElementById('share-btn');
    if (shareBtn) {
        shareBtn.addEventListener('click', shareResults);
    }
});

function displayResults(data) {
    const reportWrapper = document.querySelector('.report-wrapper');
    if (!reportWrapper) {
        console.error('Error: .report-wrapper element not found.');
        return;
    }
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
    
    if (totalVerdicts > 0) {
        // ИЗМЕНЕНИЕ: Теперь у каждого вердикта свой уникальный ID для стилизации
        const segments = [
            { type: 'True', count: verdict_counts['True'] || 0, id: 'true-segment' },
            { type: 'False', count: verdict_counts['False'] || 0, id: 'false-segment' },
            { type: 'Misleading', count: verdict_counts['Misleading'] || 0, id: 'misleading-segment' },
            { type: 'Partly True', count: verdict_counts['Partly True'] || 0, id: 'misleading-segment' }, // Тоже будет желтым
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
                progressContainer.appendChild(segmentDiv);
            }
        });
    }

    // ... (остальная часть функции displayResults остается без изменений) ...
}

function shareResults() {
    // ... (код без изменений) ...
}