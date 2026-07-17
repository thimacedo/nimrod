document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('analyze-form');
    const input = document.getElementById('target-input');
    const startBtn = document.getElementById('start-btn');
    const progressContainer = document.getElementById('progress-container');
    const progressBarFill = document.getElementById('progress-bar-fill');
    const terminalBody = document.getElementById('terminal-body');
    const steps = document.querySelectorAll('.step');

    let eventSource = null;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const target = input.value.trim();
        if (!target) return;

        // Reset UI
        startBtn.classList.add('loading');
        input.disabled = true;
        progressContainer.classList.remove('hidden');
        terminalBody.innerHTML = '';
        updateProgress(0, 'coleta');
        
        appendLog('Sistema', `Solicitando análise para o alvo: ${target}`, 'system-msg');

        try {
            const response = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target })
            });
            const data = await response.json();
            
            if (data.job_id) {
                if (data.status === 'already_running') {
                    appendLog('Sistema', 'A análise já estava em andamento. Conectando aos logs...', 'system-msg');
                }
                connectToStream(data.job_id);
            } else {
                throw new Error('Falha ao iniciar processo');
            }
        } catch (error) {
            appendLog('Erro', error.message, 'log-level-error');
            resetUI();
        }
    });

    function connectToStream(jobId) {
        if (eventSource) eventSource.close();

        eventSource = new EventSource(`/api/stream/${jobId}`);

        eventSource.onmessage = (event) => {
            const line = event.data;
            if (line === '[END_OF_STREAM]') {
                eventSource.close();
                appendLog('Sistema', 'Processo finalizado.', 'system-msg');
                updateProgress(100, 'relatorio', true);
                resetUI();
                return;
            }

            parseAndAppendLog(line);
        };

        eventSource.onerror = () => {
            appendLog('Sistema', 'Conexão com os logs finalizada/perdida.', 'log-level-warning');
            eventSource.close();
            resetUI();
        };
    }

    function parseAndAppendLog(line) {
        // Regex para extrair info (ex: 19:32:44 | INFO | nimrod | ...)
        const regex = /^(\d{2}:\d{2}:\d{2})\s*\|\s*([A-Za-z]+)\s*\|\s*([\w.]+)\s*\|\s*(.*)/;
        const match = line.match(regex);

        if (match) {
            const [_, time, level, module, message] = match;
            
            let levelClass = '';
            if (level === 'INFO') levelClass = 'log-level-info';
            if (level === 'WARNING') levelClass = 'log-level-warning';
            if (level === 'ERROR') levelClass = 'log-level-error';

            // Escape HTML basic
            const safeMessage = message.replace(/</g, '&lt;').replace(/>/g, '&gt;');

            const logHtml = `
                <div class="log-line">
                    <span class="log-time">[${time}]</span>
                    <span class="${levelClass}">[${level}]</span> 
                    <span style="color:#8172ea;">[${module}]</span> ${safeMessage}
                </div>
            `;
            terminalBody.insertAdjacentHTML('beforeend', logHtml);
            
            checkProgressHeuristics(safeMessage, module);

        } else {
            const safeLine = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            terminalBody.insertAdjacentHTML('beforeend', `<div class="log-line">${safeLine}</div>`);
            checkProgressHeuristics(safeLine, '');
        }

        autoScroll();
    }

    function checkProgressHeuristics(msg, module) {
        msg = msg.toLowerCase();
        
        if (msg.includes('iniciando an') || msg.includes('sessão')) {
            updateProgress(5, 'coleta');
        } else if (msg.includes('extraídos') || msg.includes('coletados')) {
            updateProgress(25, 'coleta', true);
            updateProgress(30, 'classificacao');
        }
        else if (msg.includes('classificados') || msg.includes('cascata')) {
            updateProgress(45, 'classificacao', true);
            updateProgress(50, 'linguistica');
        }
        else if (module.includes('stanza') || msg.includes('n-gramas') || msg.includes('linguística')) {
            updateProgress(65, 'linguistica', true);
            updateProgress(70, 'comportamento');
        }
        else if (module.includes('behavior') || module.includes('clustering') || msg.includes('cluster') || msg.includes('bot')) {
            updateProgress(85, 'comportamento', true);
            updateProgress(90, 'relatorio');
        }
        else if (msg.includes('gerando relat') || msg.includes('pdf')) {
            updateProgress(95, 'relatorio');
        }
        else if (msg.includes('salvo em') || msg.includes('concluído em')) {
            updateProgress(100, 'relatorio', true);
        }
    }

    function updateProgress(percent, currentStepName, completeCurrent = false) {
        progressBarFill.style.width = `${percent}%`;

        let passed = true;
        steps.forEach(step => {
            const stepName = step.getAttribute('data-step');
            
            if (passed) {
                if (stepName === currentStepName) {
                    passed = false;
                    step.classList.add('active');
                    if (completeCurrent) {
                        step.classList.remove('active');
                        step.classList.add('completed');
                    }
                } else {
                    step.classList.remove('active');
                    step.classList.add('completed');
                }
            } else {
                step.classList.remove('active', 'completed');
            }
        });
    }

    function appendLog(level, message, cssClass) {
        const time = new Date().toLocaleTimeString('pt-BR');
        const logHtml = `
            <div class="log-line ${cssClass}">
                <span class="log-time">[${time}]</span> [${level}] ${message}
            </div>
        `;
        terminalBody.insertAdjacentHTML('beforeend', logHtml);
        autoScroll();
    }

    function autoScroll() {
        terminalBody.scrollTop = terminalBody.scrollHeight;
    }

    function resetUI() {
        startBtn.classList.remove('loading');
        input.disabled = false;
    }
});
