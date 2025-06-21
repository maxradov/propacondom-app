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
    // This function is almost identical to the one we wrote earlier
    // It just takes the data and renders it on the page
    const resultSection = document.getElementById('result-section');
    const progressContainer = document.getElementById('progress-container');
    const confidenceContainer = document.getElementById('confidence-container');
    const reportContainer = document.getElementById('report-container');

    if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
        reportContainer.innerHTML = '<p>Error: Received incorrect report data format.</p>';
        return;
    }

    const { verdict_counts, detailed_results, summary_data, average_confidence } = data;

    // Render the Progress Bar
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

    // Display average confidence
    if (average_confidence !== undefined) {
        confidenceContainer.textContent = `Average confidence: ${average_confidence}%`;
    }

    // Render the Text Report
    let reportHTML = `
        <div id="report-summary">
            <h2>${summary_data.overall_verdict || ''}</h2>
            <p>${summary_data.overall_assessment || ''}</p>
            <ul>
                ${(summary_data.key_points || []).map(point => `<li>${point}</li>`).join('')}
            </ul>
        </div>
        <button