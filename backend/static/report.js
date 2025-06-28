document.addEventListener('DOMContentLoaded', () => {
    const reportWrapper = document.querySelector('.report-wrapper');
    if (!reportWrapper) return;

    // Получаем analysisId из url
    const analysisId = window.location.pathname.split('/').pop();
    fetch(`/api/report/${analysisId}`)
        .then(res => res.json())
        .then(data => {
            if (data.status === "PENDING_SELECTION") {
                renderClaimSelectionUI(data); // Показать выбор клеймов прямо в report
            } else {
                displayResults(data); // Обычный отчёт
            }
        })
        .catch(err => {
            const reportContainer = reportWrapper.querySelector('#report-container');
            if (reportContainer) {
                reportContainer.innerHTML = `<p style="color:red;">${err.message || "Failed to load report."}</p>`;
            }
        });

    // Share logic
    const shareBtn = document.getElementById('share-btn');
    if (shareBtn) {
        shareBtn.addEventListener('click', shareResults);
    }
});

// ----- Отображение выбора клеймов прямо в report.html -----
function renderClaimSelectionUI(analysisData) {
    const reportContainer = document.getElementById('report-container');
    const confidenceContainer = document.getElementById('confidence-container');
    if (confidenceContainer) confidenceContainer.innerHTML = '';
    if (!reportContainer) return;

    const analysisId = analysisData.id;
    const claims = analysisData.claims_for_selection;

    const videoTitle = analysisData.video_title || "";
    const thumbnailUrl = analysisData.thumbnail_url || "";
    const sourceUrl = analysisData.source_url || "";


    if (!claims || claims.length === 0) {
        reportContainer.innerHTML = `<p>${window.translations.no_claims_found || 'Could not extract any claims to check.'}</p>`;
        return;
    }

    let claimsHTML = `
        <h3>${(window.translations.select_claims_title || 'Select up to 5 claims to verify')}</h3>
        <div class="claims-list">`;

    claims.forEach((claimData, index) => {
        const { hash, text, is_cached, cached_data } = claimData;
        const sanitizedText = text.replace(/"/g, '&quot;');
        if (is_cached) {
            let verdictText = '';
            if (cached_data && cached_data.verdict) {
                verdictText = `${window.translations.already_checked || 'Already checked'}: ${cached_data.verdict}`;
            } else {
                verdictText = window.translations.already_checked || 'Already checked';
            }
            claimsHTML += `
                <div class="claim-checkbox-item cached">
                    <input type="checkbox" id="claim-${index}" value="${hash}" checked disabled>
                    <label for="claim-${index}">${text} <span>(${verdictText})</span></label>
                </div>`;
        } else {
            claimsHTML += `
                <div class="claim-checkbox-item">
                    <input type="checkbox" id="claim-${index}" name="claim-to-check" value="${hash}" data-text="${sanitizedText}">
                    <label for="claim-${index}">${text}</label>
                </div>`;
        }
    });

    claimsHTML += `</div><button id="run-selected-factcheck-btn" disabled>${(window.translations.fact_check_selected_button || 'Fact-Check Selected')}</button>`;

    reportContainer.innerHTML = claimsHTML;

    const checkBoxes = reportContainer.querySelectorAll('input[name="claim-to-check"]');
    const runCheckBtn = document.getElementById('run-selected-factcheck-btn');
    const MAX_CLAIMS_TO_CHECK = 5;

    checkBoxes.forEach(box => {
        box.addEventListener('change', (e) => {
            const selected = Array.from(checkBoxes).filter(i => i.checked);
            if (selected.length > MAX_CLAIMS_TO_CHECK) {
                e.preventDefault();
                box.checked = false;
                alert(`${(window.translations.limit_selection_alert || 'You can only select up to')} ${MAX_CLAIMS_TO_CHECK} ${(window.translations.claims_alert || 'claims')}.`);
            }
            runCheckBtn.disabled = selected.length === 0;
        });
    });

    runCheckBtn.addEventListener('click', () => {
        const selectedClaimsData = Array.from(checkBoxes)
            .filter(i => i.checked)
            .map(i => ({ hash: i.value, text: i.dataset.text }));
        startSelectedFactCheck(analysisId, selectedClaimsData);
    });
}

// --- Обычный отчёт ---
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
		<a href="#" id="toggle-unchecked-claims" class="details-toggle" style="margin-left: 1rem;">Show unchecked claims</a>
		<div id="unchecked-claims-container" style="display: none; margin-top: 1.3rem;"></div>
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
	
	const toggleUncheckedLink = document.getElementById('toggle-unchecked-claims');
	const uncheckedClaimsContainer = document.getElementById('unchecked-claims-container');
	if (toggleUncheckedLink && uncheckedClaimsContainer) {
		toggleUncheckedLink.addEventListener('click', (e) => {
			e.preventDefault();
			if (uncheckedClaimsContainer.style.display === 'none') {
				renderUncheckedClaimsSection(data, uncheckedClaimsContainer);
				uncheckedClaimsContainer.style.display = 'block';
				toggleUncheckedLink.textContent = 'Hide unchecked claims';
			} else {
				uncheckedClaimsContainer.style.display = 'none';
				toggleUncheckedLink.textContent = 'Show unchecked claims';
			}
		});
	}


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

function renderUncheckedClaimsSection(data, container) {
    const extracted = data.extracted_claims || [];
    // Собираем ВСЕ хэши проверенных claims (ищем и .hash, и .claim_hash, и fallback по claim-тексту)
    const checkedHashes = new Set(
        (data.detailed_results || []).map(item => item.hash || item.claim_hash)
    );
    // Для надёжности, если detailed_results не содержит hash, делаем fallback по claim-тексту:
    const checkedTexts = new Set(
        (data.detailed_results || []).map(item => (item.claim || "").trim())
    );
    // Фильтруем только те, которых нет среди проверенных по хэшу И тексту
    const uncheckedClaims = extracted.filter(claim =>
        (!checkedHashes.has(claim.hash)) &&
        (!checkedTexts.has((claim.text || "").trim()))
    );
    if (!uncheckedClaims.length) {
        container.innerHTML = `<div style="color: #6c757d;">All claims have been checked.</div>`;
        return;
    }
    let html = `<form id="unchecked-claims-form"><div class="claims-list">`;
    uncheckedClaims.forEach((claim, idx) => {
        html += `
            <div class="claim-checkbox-item">
                <input type="checkbox" id="unchecked-${idx}" name="unchecked-to-check" value="${claim.hash}" data-text="${claim.text.replace(/"/g, '&quot;')}">
                <label for="unchecked-${idx}">${claim.text}</label>
            </div>
        `;
    });
    html += `</div>
        <button id="check-more-claims-btn" type="submit" disabled style="margin-top:1rem;">Check more claims</button>
    </form>`;
    container.innerHTML = html;

    const checkBoxes = container.querySelectorAll('input[name="unchecked-to-check"]');
    const submitBtn = container.querySelector('#check-more-claims-btn');
    const MAX_CLAIMS = 5;
    checkBoxes.forEach(box => {
        box.addEventListener('change', () => {
            const selected = Array.from(checkBoxes).filter(i => i.checked);
            if (selected.length > MAX_CLAIMS) {
                box.checked = false;
                alert(`You can only select up to ${MAX_CLAIMS} claims.`);
            }
            submitBtn.disabled = selected.length === 0;
        });
    });

    container.querySelector('#unchecked-claims-form').addEventListener('submit', function(e) {
        e.preventDefault();
        const selectedClaimsData = Array.from(checkBoxes)
            .filter(i => i.checked)
            .map(i => ({ hash: i.value, text: i.dataset.text }));
        startSelectedFactCheck(data.id, selectedClaimsData);
    });
}



function startSelectedFactCheck(analysisId, claimsData) {
    const reportContainer = document.getElementById('report-container');
    const confidenceContainer = document.getElementById('confidence-container');
    if (confidenceContainer) confidenceContainer.innerHTML = '';
    reportContainer.innerHTML = `<p>${(window.translations.sending_request_for_checking || 'Sending selected claims for final analysis...')}</p>`;
    fetch('/api/fact_check_selected', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysis_id: analysisId, selected_claims_data: claimsData })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(errData => {
                throw new Error(errData.error || errData.result || (window.translations.error_starting || 'Error starting analysis.'));
            });
        }
        return response.json();
    })
    .then(data => {
        // После завершения анализа — перегрузи страницу, чтобы получить готовый отчёт
        if (data.task_id) {
            pollStatus(data.task_id, analysisId);
        }
    })
    .catch(error => {
        reportContainer.innerHTML = `<p style="color:red;">Error: ${error.message}</p>`;
    });
}

// Poll статус выполнения fact_check_selected и показывай репорт после завершения
function pollStatus(taskId, analysisId) {
    const interval = setInterval(() => {
        fetch(`/api/status/${taskId}`)
            .then(res => res.json())
            .then(data => {
                if (data.status === 'SUCCESS') {
                    clearInterval(interval);
                    // Просто перегрузи страницу для показа готового отчёта
                    window.location.reload();
                }
                if (data.status === 'FAILURE') {
                    clearInterval(interval);
                    const reportContainer = document.getElementById('report-container');
                    if (reportContainer) {
                        reportContainer.innerHTML = `<p style="color:red;">${data.result || 'Fact-check failed.'}</p>`;
                    }
                }
            })
            .catch(() => clearInterval(interval));
    }, 3000);
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
