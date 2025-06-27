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
    if (!reportWrapper) { return; }

    const confidenceContainer = reportWrapper.querySelector('#confidence-container');
    const reportContainer = reportWrapper.querySelector('#report-container');

    if (!confidenceContainer || !reportContainer) {
        reportContainer.innerHTML = '<p>Error: One or more report containers are missing.</p>';
        return;
    }
    if (!data || !data.detailed_results || !data.verdict_counts || !data.summary_data) {
        reportContainer.innerHTML = '<p>Error: Received incorrect report data format.</p>';
        return;
    }

    const { verdict_counts, detailed_results, summary_data } = data;
	const totalClaims = detailed_results.length;
	let totalClaimsHTML = `<div class="checked-claims-total" style="font-size:1.05rem;color:#6c757d;margin-bottom:0.35em;">
		${window.translations.checked_claims_total || 'Checked claims'}: <strong>${totalClaims}</strong>
	</div>`;
    const verdictOrder = [
        { key: 'True', icon: '✅', label: window.translations.true_label || 'Confirmed' },
        { key: 'False', icon: '❌', label: window.translations.false_label || 'Refuted' },
        { key: 'Misleading', icon: '⚠️', label: window.translations.misleading_label || 'Misleading' },
        { key: 'Partly True', icon: '🟡', label: window.translations.partly_true_label || 'Partly True' },
        { key: 'Unverifiable', icon: '❓', label: window.translations.unverifiable_label || 'Unverifiable' }
    ];

    let iconsSummary = '';
    verdictOrder.forEach(v => {
        if (verdict_counts[v.key] && verdict_counts[v.key] > 0) {
            iconsSummary += `<span class="verdict-icon verdict-${v.key.replace(/\s/g, '-')}">${v.icon} ${verdict_counts[v.key]} ${v.label}</span> `;
        }
    });

    let showText = '';
    if (data.input_type === 'text' && data.user_text) {
        const text = data.user_text;
        showText = `
            <div class="original-text">
                <label>${window.translations.original_text || 'Checked Text'}:</label>
                <textarea rows="5" readonly style="width:100%;resize:vertical;">${text.length > 800 ? text.substring(0, 800) + '...' : text}</textarea>
            </div>
        `;
    }

    let reportHTML = `
        <div id="report-summary">
            <div class="verdict-summary-icons">${iconsSummary}</div>
			${totalClaimsHTML}
            <h2>${summary_data.overall_verdict || ''}</h2>

            <div class="disclaimer-box">
                <p><strong>${window.translations.disclaimer_title || 'Disclaimer:'}</strong> ${window.translations.disclaimer_text || 'This report is AI-generated. It may contain errors and may not have access to real-time data. Please verify the information independently.'}</p>
            </div>

            <p>${summary_data.overall_assessment || ''}</p>
            ${showText}
            <ul>
                ${(summary_data.key_points || []).map(point => `<li>${point}</li>`).join('')}
            </ul>
        </div>
        <button id="details-toggle" class="details-toggle">${window.translations.show_detailed}</button>
        <div id="claim-list-container" style="display: none;">
            <div class="claim-list">
    `;

    detailed_results.forEach(claim => {
        const verdictClass = (claim.verdict || 'No-data').replace(/[\s/]+/g, '-');
        reportHTML += `
            <div class="claim-item verdict-${verdictClass}">
                <p class="claim-text">${claim.claim || (window.translations.claim_text_missing || 'Claim text missing')}
                    <span class="claim-verdict">${claim.verdict || (window.translations.no_verdict || 'No verdict')}</span>
                </p>
                <div class="claim-explanation"><p>${claim.explanation || ''}</p></div>
        `;

        if (claim.sources && claim.sources.length > 0) {
            reportHTML += `<div class="claim-sources"><strong>${window.translations.sources}</strong><br>`;
            claim.sources.forEach(source => {
                let domain = '';
                try {
                    domain = (new URL(source)).hostname.replace(/^www\./, '');
                } catch (e) { domain = ''; }

                reportHTML += `<div class="source-item">
                    <a href="${source}" target="_blank" rel="noopener noreferrer">${domain || source}</a>
                </div>`;
            });
            reportHTML += `</div>`;
        }
        reportHTML += `</div>`;
    });
    reportHTML += `</div></div>`;
    reportContainer.innerHTML = reportHTML;

    confidenceContainer.innerHTML = '';

    const toggleButton = document.getElementById('details-toggle');
    const detailsContainer = document.getElementById('claim-list-container');
    if (toggleButton && detailsContainer) {
        toggleButton.addEventListener('click', () => {
            const isHidden = detailsContainer.style.display === 'none';
            detailsContainer.style.display = isHidden ? 'block' : 'none';
            toggleButton.textContent = isHidden ? window.translations.hide_detailed : window.translations.show_detailed;
        });
    }
}

function shareResults() {
    const shareData = {
        title: window.translations.share_report,
        text: window.translations.share_text,
        url: window.location.href
    };
    const shareBtn = document.getElementById('share-btn');

    if (navigator.share) {
        navigator.share(shareData).catch(console.log);
    } else {
        navigator.clipboard.writeText(window.location.href).then(() => {
            const originalText = shareBtn.textContent;
            shareBtn.textContent = window.translations.link_copied;
            setTimeout(() => shareBtn.textContent = originalText, 2000);
        }).catch(console.error);
    }
}
