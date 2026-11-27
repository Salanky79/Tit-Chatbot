/* chat.js: hành vi giao diện; tải đồng bộ tại vị trí script cũ.
 * Các khối giữ nguyên thứ tự để bảo toàn cascade và thứ tự khởi tạo.
 */

    // === KHỐI 01: Tiện ích hiển thị và điều hướng sidebar ===
    function escapeHtml(str) {
        if (!str) return "";
        return String(str).replace(/[&<>"']/g, m => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[m]));
    }

    function toggleSidebar() {
        const sidebar = document.querySelector(".sidebar");
        if (!sidebar) return;
        if (window.innerWidth <= 768) {
            sidebar.classList.toggle("open");
        } else {
            sidebar.classList.toggle("collapsed");
            try {
                localStorage.setItem("sidebar_collapsed", sidebar.classList.contains("collapsed") ? "true" : "false");
            } catch (e) {}
        }
    }

    // Phím tắt Ctrl+B hoặc Cmd+B để đóng/mở thanh bên
    window.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
            const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
            if (tag !== "input" && tag !== "textarea") {
                e.preventDefault();
                toggleSidebar();
            }
        }
    });

    // Close sidebar when clicking outside on mobile
    document.addEventListener("click", (e) => {
        const sidebar = document.querySelector(".sidebar");
        const toggle = document.getElementById("sidebarToggle");
        const collapseBtn = document.getElementById("sidebarCollapseBtn");
        if (window.innerWidth <= 768 && sidebar && sidebar.classList.contains("open")) {
            if (!sidebar.contains(e.target) && (!toggle || !toggle.contains(e.target)) && (!collapseBtn || !collapseBtn.contains(e.target))) {
                sidebar.classList.remove("open");
            }
        }
    });

    // ---- Configure marked.js ----
    marked.setOptions({
        breaks: true,       // \n → <br> inside paragraphs
        gfm: true,          // GitHub Flavored Markdown
        highlight: function(code, lang) {
            try {
                if (typeof hljs !== "undefined" && lang && hljs.getLanguage && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                if (typeof hljs !== "undefined" && hljs.highlightAuto) {
                    return hljs.highlightAuto(code).value;
                }
            } catch (e) {
                console.warn("highlight.js error:", e);
            }
            return code;
        }
    });

    /** Render Markdown string to sanitized HTML and apply to an element with KaTeX math support. */
    // === KHỐI 02: Render Markdown, công thức và code block ===
    function renderMarkdown(el, text) {
        if (!text) {
            el.innerHTML = "";
            return;
        }

        try {
            let content = String(text);

            // 1. Chuẩn hóa các dạng công thức lỏng lẻo từ LLM hoặc copy-paste:
            // Khối [ \n ... \n ] chứa từ khóa LaTeX (bị rụng dấu gạch chéo ngược)
            content = content.replace(/(?:^|\n)\[\s*\n([\s\S]*?\\[a-zA-Z]+[\s\S]*?)\n\s*\](?:\n|$)/g, '\n\n$$\n$1\n$$\n\n');
            // Chuẩn hóa \[ ... \] -> $$ ... $$
            content = content.replace(/\\\[([\s\S]*?)\\\]/g, '$$$$$1$$$$');
            // Chuẩn hóa \( ... \) -> $ ... $
            content = content.replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$');
            // Chuẩn hóa ( \frac... ) hoặc ( \binom... ) -> $ ... $
            content = content.replace(/\((\s*\\[a-zA-Z]+(?:\{[^{}]*\}|[^\)\n])*)\)/g, '$$$1$$');

            // 2. Bảo vệ các khối công thức khỏi marked.js (tránh bị parse nhầm _ thành <em>, \n thành <br>)
            const mathItems = [];
            // Khối công thức Display Math ($$ ... $$)
            content = content.replace(/\$\$([\s\S]*?)\$\$/g, function(match, formula) {
                const token = `@@MATH_BLOCK_${mathItems.length}@@`;
                mathItems.push({ token, formula: formula.trim(), display: true });
                return token;
            });
            // Công thức nội dòng Inline Math ($ ... $)
            content = content.replace(/\$([^\$\n\r]+?)\$/g, function(match, formula) {
                // Bỏ qua giá tiền tệ như $100 hoặc $50.5
                const cleanFormula = formula.trim().replace(",", ".");
                if (!isNaN(cleanFormula) && cleanFormula !== "") return match;
                const token = `@@MATH_INLINE_${mathItems.length}@@`;
                mathItems.push({ token, formula: formula.trim(), display: false });
                return token;
            });

            // 3. Parse Markdown sang HTML
            let html = marked.parse(content);

            // 4. Thay thế các placeholder bằng HTML do KaTeX render
            for (const item of mathItems) {
                let rendered = "";
                if (typeof katex !== "undefined" && katex.renderToString) {
                    try {
                        rendered = katex.renderToString(item.formula, {
                            displayMode: item.display,
                            throwOnError: false,
                            output: "htmlAndMathml"
                        });
                    } catch (err) {
                        rendered = item.display ? `$$${item.formula}$$` : `$${item.formula}$`;
                    }
                } else {
                    rendered = item.display ? `$$${item.formula}$$` : `$${item.formula}$`;
                }
                html = html.replace(item.token, rendered);
            }

            el.innerHTML = html;
        } catch (e) {
            console.warn("renderMarkdown error:", e);
            el.textContent = text || "";
            return;
        }

        // Syntax highlight all code blocks if hljs is available
        if (typeof hljs !== "undefined" && hljs.highlightElement) {
            el.querySelectorAll("pre code").forEach(block => {
                try {
                    hljs.highlightElement(block);
                } catch (e) {}
            });
        }

        // Add copy button to every <pre>
        el.querySelectorAll("pre").forEach(pre => {
            if (pre.querySelector(".copy-btn")) return; // already added
            const btn = document.createElement("button");
            btn.className = "copy-btn";
            btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> <span>Copy</span>`;
            btn.onclick = () => {
                const code = pre.querySelector("code");
                navigator.clipboard.writeText(code ? code.innerText : pre.innerText)
                    .then(() => {
                        btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> <span style="color:#10b981">Copied!</span>`;
                        setTimeout(() => {
                            btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> <span>Copy</span>`;
                        }, 2000);
                    })
                    .catch(() => {
                        btn.textContent = "Error";
                    });
            };
            pre.appendChild(btn);
        });

        // Open links in new tab safely
        el.querySelectorAll("a").forEach(a => {
            a.target = "_blank";
            a.rel = "noopener noreferrer";
        });
    }

    const chatContainer = document.getElementById("chatContainer");
    const messageInput = document.getElementById("messageInput");
    const sendBtn = document.getElementById("sendBtn");
    const statusText = document.getElementById("status");
    const conversationList = document.getElementById("conversationList");
    const modelSelect = document.getElementById("modelSelect");
    const noticeText = document.getElementById("noticeText");
    const micBtn = document.getElementById("micBtn");

    // Dynamic dot pulsing when status changes
    const statusObserver = new MutationObserver(() => {
        const text = (statusText.textContent || "").trim().toLowerCase();
        if (text === "ready") {
            statusText.classList.remove("busy", "listening");
        } else if (text.includes("listening")) {
            statusText.classList.remove("busy");
            statusText.classList.add("listening");
        } else {
            statusText.classList.remove("listening");
            statusText.classList.add("busy");
        }
    });
    if (statusText) {
        statusObserver.observe(statusText, { childList: true, characterData: true, subtree: true });
    }

    let pendingFile = null;
    let isTemporaryChat = false;
    let threadId = localStorage.getItem("thread_id");
    let activeProjectId = localStorage.getItem("active_project_id") || "";
    let activeProject = null;
    let cachedProjects = [];
    let pendingAddThreadId = "";
    let projectMembersState = null;
    let projectLoadVersion = 0;
    let projectEventsSource = null;
    let projectEventsUserId = "";
    let projectEventsFallback = null;
    let projectRefreshRunning = false;
    let projectRefreshPending = false;
    let projectMembersRefreshPending = false;
    let pendingProjectInvitationToken = sessionStorage.getItem("pending_project_invitation") || "";
    let projectInvitationState = null;

    /**
     * Cập nhật thanh workspace Project, khu vực chat của Project ở sidebar và trạng thái nút Temporary chat.
     */
    // === KHỐI 03: Workspace Project và chế độ chat tạm thời ===
    function updateProjectWorkspace() {
        const isInProject = Boolean(activeProjectId && activeProject);
        const bar = document.getElementById("projectWorkspaceBar");
        const name = document.getElementById("projectWorkspaceName");
        const tempBtn = document.getElementById("tempChatToggle");
        const projectChatSection = document.getElementById("projectChatSection");
        const projectChatTitle = document.getElementById("projectChatSectionTitle");
        const personalTitle = document.getElementById("personalChatSectionTitle");

        if (bar) bar.classList.toggle("active", isInProject);
        if (name) name.textContent = activeProject ? activeProject.name : "";
        if (tempBtn) tempBtn.style.display = activeProjectId ? "none" : "inline-flex";
        if (projectChatSection) projectChatSection.classList.toggle("active", isInProject);
        if (projectChatTitle) {
            projectChatTitle.innerHTML = isInProject
                ? `<span>Chat trong ${escapeHtml(activeProject.name || "Project")}</span>`
                : `<span>Chat trong Project</span>`;
        }
        if (personalTitle) personalTitle.style.display = currentUser ? "flex" : "none";

        const deleteBtn = document.getElementById("projectDeleteBtn");
        if (deleteBtn) deleteBtn.style.display = activeProject?.membership?.role === "owner" ? "inline-flex" : "none";
        if (projectMembersState && (!currentUser || activeProjectId !== projectMembersState.projectId)) {
            closeProjectMembersModal();
        }
    }

    function toggleTemporaryChat() {
        if (activeProjectId) return;
        isTemporaryChat = !isTemporaryChat;
        const btn = document.getElementById("tempChatToggle");
        if (btn) {
            btn.classList.toggle("active", isTemporaryChat);
        }
        renderWelcome();
        if (messageInput) messageInput.focus();
    }

    function updateTemporaryChatVisibility() {
        const tempBtn = document.getElementById("tempChatToggle");
        if (!tempBtn) return;
        if (activeProjectId) { tempBtn.style.display = "none"; return; }
        const hasMessages = Boolean(chatContainer && chatContainer.querySelector(".message"));
        tempBtn.style.display = hasMessages ? "none" : "inline-flex";
        tempBtn.classList.toggle("active", isTemporaryChat);
    }

    if (!threadId) {
        threadId = crypto.randomUUID();
        localStorage.setItem("thread_id", threadId);
    }

    const savedModel = localStorage.getItem("selected_model");

    if (savedModel && modelSelect) {
        modelSelect.value = savedModel;
    }

    if (modelSelect) {
        modelSelect.addEventListener("change", () => {
            localStorage.setItem("selected_model", modelSelect.value);

            if (noticeText) {
                noticeText.textContent = `Selected model: ${modelSelect.value}`;
            }
        });
    }

    let recognition = null;
    let isDictating = false;

    // === KHỐI 04: Nhập tin nhắn bằng giọng nói ===
    function setupSpeechRecognition() {
        const SpeechRecognition =
            window.SpeechRecognition || window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            return null;
        }

        const speechRecognition = new SpeechRecognition();

        speechRecognition.lang = "en-US";
        speechRecognition.continuous = true;
        speechRecognition.interimResults = true;

        speechRecognition.onstart = () => {
            isDictating = true;

            if (micBtn) {
                micBtn.classList.add("recording");
                micBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>`;
            }

            statusText.textContent = "Listening...";
        };

        speechRecognition.onresult = (event) => {
            let finalTranscript = "";
            let interimTranscript = "";

            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;

                if (event.results[i].isFinal) {
                    finalTranscript += transcript + " ";
                } else {
                    interimTranscript += transcript;
                }
            }

            if (finalTranscript) {
                const currentText = messageInput.value.trim();

                messageInput.value = currentText
                    ? currentText + " " + finalTranscript.trim()
                    : finalTranscript.trim();

                autoResize(messageInput);
            }

            if (interimTranscript && noticeText) {
                noticeText.textContent = "Listening: " + interimTranscript;
            }
        };

        speechRecognition.onerror = (event) => {
            console.error("Speech recognition error:", event.error);

            if (event.error === "not-allowed") {
                alert("Microphone permission denied. Please allow microphone access.");
            }

            stopDictation();
        };

        speechRecognition.onend = () => {
            if (isDictating) {
                try {
                    speechRecognition.start();
                } catch (error) {
                    stopDictation();
                }
            }
        };

        return speechRecognition;
    }

    function toggleDictation() {
        if (!recognition) {
            recognition = setupSpeechRecognition();
        }

        if (!recognition) {
            alert("Speech recognition is not supported in this browser. Please use Chrome or Edge.");
            return;
        }

        if (isDictating) {
            stopDictation();
        } else {
            startDictation();
        }
    }

    function startDictation() {
        try {
            recognition.start();
        } catch (error) {
            console.error("Could not start dictation:", error);
        }
    }

    function stopDictation() {
        isDictating = false;

        if (recognition) {
            try {
                recognition.stop();
            } catch (error) {
                console.error("Could not stop dictation:", error);
            }
        }

        if (micBtn) {
            micBtn.classList.remove("recording");
            micBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>`;
        }

        statusText.textContent = "Ready";

        if (noticeText) {
            noticeText.textContent = "Tit can make mistakes. Check important info.";
        }

        messageInput.focus();
    }

    // === KHỐI 05: Ô nhập tin và file đính kèm ===
    function autoResize(textarea) {
        textarea.style.height = "auto";
        textarea.style.height = textarea.scrollHeight + "px";
    }

    function hideWelcome() {
        const welcome = document.getElementById("welcome");
        const cards = document.getElementById("cards");

        if (welcome) welcome.style.display = "none";
        if (cards) cards.style.display = "none";

        const tempBtn = document.getElementById("tempChatToggle");
        if (tempBtn) tempBtn.style.display = "none";
    }

    function handleKeyDown(event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    }

    function usePrompt(text) {
        messageInput.value = text;
        autoResize(messageInput);
        messageInput.focus();
    }

    function openFilePicker() {
        document.getElementById("fileInput").click();
    }

    function clearPendingAttachment() {
        pendingFile = null;
        const fileInput = document.getElementById("fileInput");
        if (fileInput) fileInput.value = "";
        const preview = document.getElementById("attachmentPreview");
        if (preview) {
            preview.innerHTML = "";
            preview.hidden = true;
        }
        const row = document.getElementById("attachmentRow");
        if (row) {
            row.hidden = true;
        }
    }

    function showPendingAttachment(file) {
        const row = document.getElementById("attachmentRow");
        const preview = document.getElementById("attachmentPreview");
        if (!preview) return;

        preview.innerHTML = "";

        const isImg = isImageFile(file);
        const iconSvg = isImg
            ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ff8a94" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>`
            : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ff8a94" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>`;

        const icon = document.createElement("span");
        icon.className = "attachment-icon";
        icon.innerHTML = iconSvg;

        const name = document.createElement("span");
        name.className = "attachment-name";
        name.textContent = `Attached: ${file.name}`;
        name.title = file.name;

        const remove = document.createElement("button");
        remove.className = "remove-attachment";
        remove.type = "button";
        remove.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`;
        remove.title = "Remove attachment";
        remove.onclick = clearPendingAttachment;

        preview.append(icon, name, remove);
        preview.hidden = false;
        if (row) {
            row.hidden = false;
        }
    }

    function isImageFile(file) {
        const ext = (file.name || "").split(".").pop().toLowerCase();
        return file.type.startsWith("image/") || ["jpg", "jpeg", "png", "webp"].includes(ext);
    }

    // === KHỐI 06: Hiển thị tin nhắn và tác giả ===
    function addMessage(role, content = "", senderName = "") {
        hideWelcome();

        const messageDiv = document.createElement("div");
        messageDiv.className = role === "user"
            ? "message user"
            : "message assistant";

        const avatar = document.createElement("div");
        avatar.className = role === "user"
            ? "avatar user-avatar"
            : "avatar bot-avatar";

        if (role === "user") {
            if (currentUser && currentUser.avatar_url) {
                avatar.className = "avatar user-avatar has-custom-avatar";
                avatar.innerHTML = `<img src="${escapeHtml(currentUser.avatar_url)}" class="chat-user-avatar-img" alt="User" onerror="this.parentElement.classList.remove('has-custom-avatar'); this.outerHTML='<svg width=\\'17\\' height=\\'17\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'currentColor\\' stroke-width=\\'2.2\\' stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\'><path d=\\'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2\\'></path><circle cx=\\'12\\' cy=\\'7\\' r=\\'4\\'></circle></svg>'">`;
            } else {
                avatar.className = "avatar user-avatar";
                avatar.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>`;
            }
        } else {
            avatar.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path></svg>`;
        }

        const messageContent = document.createElement("div");
        messageContent.className = "message-content";
        if (role === "user") {
            // User messages: plain text (no Markdown)
            if (senderName) {
                const authorLabel = document.createElement("span");
                authorLabel.className = "message-author-label";
                authorLabel.textContent = senderName;
                messageContent.appendChild(authorLabel);
            }
            const userText = document.createElement("div");
            userText.textContent = content;
            messageContent.appendChild(userText);
        } else {
            // Assistant messages: render Markdown → HTML
            if (content) {
                renderMarkdown(messageContent, content);
            }
        }

        messageDiv.appendChild(avatar);
        messageDiv.appendChild(messageContent);

        chatContainer.appendChild(messageDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;

        return messageContent;
    }

    // === KHỐI 07: Gợi ý câu hỏi và request chạy nền ===
    function addFollowupSuggestions(questions, assistantMessage) {
        const cleanQuestions = questions
            .filter(question => typeof question === "string" && question.trim())
            .slice(0, 2);
        if (cleanQuestions.length !== 2) return false;

        const oldSuggestions = assistantMessage.parentElement.nextElementSibling;
        if (oldSuggestions && oldSuggestions.classList.contains("followup-suggestions")) {
            oldSuggestions.remove();
        }

        const container = document.createElement("div");
        container.className = "followup-suggestions";

        const label = document.createElement("span");
        label.className = "followup-suggestions-label";
        label.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg> Suggested prompts`;
        container.appendChild(label);

        for (const question of cleanQuestions) {
            const button = document.createElement("button");
            button.className = "followup-suggestion";
            button.type = "button";
            button.innerHTML = `<span>${escapeHtml(question)}</span> <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>`;
            button.title = "Use this prompt";
            button.onclick = () => {
                messageInput.value = question;
                autoResize(messageInput);
                messageInput.focus();
            };
            container.appendChild(button);
        }

        assistantMessage.parentElement.insertAdjacentElement("afterend", container);
        chatContainer.scrollTop = chatContainer.scrollHeight;
        return true;
    }

    // Controller quản lý việc hủy request gợi ý cũ khi người dùng gửi tin nhắn mới
    let followupAbortController = null;

    // Gửi yêu cầu sinh gợi ý câu hỏi tiếp theo (Follow-up) chạy ngầm song song với UI
    // Không làm trễ hoặc chặn tương tác của người dùng sau khi chatbot trả lời xong
    async function fetchParallelFollowups(userMessage, assistantAnswer, botElement, model) {
        // Hủy request gợi ý đang chờ (nếu có) trước khi tạo request mới
        if (followupAbortController) {
            followupAbortController.abort();
        }
        followupAbortController = new AbortController();

        try {
            const res = await fetch("/api/chat/followups", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    user_message: userMessage,
                    assistant_answer: assistantAnswer,
                    model: model
                }),
                signal: followupAbortController.signal
            });
            if (!res.ok) return;
            const data = await res.json();
            // Chỉ hiển thị khi có đúng 2 gợi ý hợp lệ để đảm bảo chất lượng UI
            if (data.success && Array.isArray(data.suggestions) && data.suggestions.length === 2) {
                addFollowupSuggestions(data.suggestions, botElement);
            }
        } catch (err) {
            // Bỏ qua lỗi AbortError khi người dùng chủ động gửi tin nhắn mới
            if (err.name !== "AbortError") {
                console.warn("Follow-up suggestions skipped or failed:", err);
            }
        }
    }

    // === KHỐI 08: Trạng thái thực thi công cụ ===
    function addToolProgress(toolName) {
        const wrapper = document.createElement("div");
        wrapper.className = "tool-progress";

        const box = document.createElement("div");
        box.className = "tool-progress-box";

        const icon = document.createElement("span");
        icon.className = "tool-spinner";

        const text = document.createElement("span");
        text.textContent = `Using ${toolName}...`;

        box.appendChild(icon);
        box.appendChild(text);
        wrapper.appendChild(box);

        chatContainer.appendChild(wrapper);
        chatContainer.scrollTop = chatContainer.scrollHeight;

        return {
            wrapper,
            icon,
            text
        };
    }

    function completeToolProgress(toolProgress, toolName) {
        if (!toolProgress) return;

        toolProgress.icon.className = "tool-check";
        toolProgress.icon.textContent = "✓";
        toolProgress.text.textContent = `${toolName} completed`;
    }

    // === KHỐI 09: Danh sách Project, quyền và lời mời ===
    async function loadProjects() {
        const version = ++projectLoadVersion;
        const userId = currentUser?.id;
        const section = document.getElementById("projectSection");
        const list = document.getElementById("projectList");
        const invitationBox = document.getElementById("projectInvitations");
        if (!section || !list) return;
        if (!currentUser) {
            stopProjectEvents();
            section.style.display = "none";
            list.innerHTML = "";
            if (invitationBox) invitationBox.innerHTML = "";
            cachedProjects = [];
            activeProjectId = "";
            activeProject = null;
            localStorage.removeItem("active_project_id");
            updateProjectWorkspace();
            return;
        }
        section.style.display = "block";
        try {
            const [projectResponse, inviteResponse] = await Promise.all([
                fetch("/api/projects"), fetch("/api/projects/invitations")
            ]);
            const projectData = await projectResponse.json();
            const inviteData = await inviteResponse.json();
            if (version !== projectLoadVersion || currentUser?.id !== userId) return;
            if (projectResponse.status === 401 || inviteResponse.status === 401) {
                currentUser = null;
                renderUserUI();
                await loadProjects();
                await newChat();
                return;
            }
            if (!projectResponse.ok || !inviteResponse.ok) throw new Error(projectData.error || inviteData.error || "Không tải được Project.");
            const projects = projectData.projects || [];
            // Lưu danh sách Project kèm membership role để dùng lại cho modal Add to Project.
            cachedProjects = projects;
            list.innerHTML = "";
            projects.forEach(project => {
                const button = document.createElement("button");
                button.className = "project-list-item" + (project._id === activeProjectId ? " active" : "");
                button.innerHTML = `<span aria-hidden="true">▣</span><span>${escapeHtml(project.name)}</span>`;
                button.onclick = () => selectProject(project._id);
                list.appendChild(button);
            });
            if (projects.length === 0) {
                list.innerHTML = `<div style="padding:6px 8px;color:var(--text-muted);font-size:12px">Chưa có Project</div>`;
            }
            if (invitationBox) {
                invitationBox.innerHTML = "";
                (inviteData.invitations || []).forEach(invite => {
                    const row = document.createElement("div");
                    row.className = "project-invite-row";
                    row.innerHTML = `<div>${escapeHtml(invite.project?.name || "Lời mời Project")} · ${invite.role === "editor" ? "Có thể chỉnh sửa" : "Chỉ trò chuyện"}</div><div class="project-invitation-expiry">${invite.expires_at ? `Hết hạn: ${escapeHtml(new Date(invite.expires_at).toLocaleString("vi-VN"))}` : ""}</div><div style="margin-top:6px"><button class="project-tool-btn">Chấp nhận</button> <button class="project-tool-btn">Từ chối</button></div>`;
                    const [accept, decline] = row.querySelectorAll("button");
                    accept.onclick = () => respondProjectInvite(invite._id, true, row);
                    decline.onclick = () => respondProjectInvite(invite._id, false, row);
                    invitationBox.appendChild(row);
                });
            }
            if (activeProjectId) {
                const found = projects.find(item => item._id === activeProjectId);
                if (!found) {
                    activeProjectId = "";
                    activeProject = null;
                    localStorage.removeItem("active_project_id");
                } else {
                    const detailResponse = await fetch(`/api/projects/${activeProjectId}`);
                    const detail = await detailResponse.json();
                    if (version !== projectLoadVersion || currentUser?.id !== userId) return;
                    if (!detailResponse.ok) {
                        activeProjectId = "";
                        activeProject = null;
                        localStorage.removeItem("active_project_id");
                        updateProjectWorkspace();
                        return;
                    }
                    activeProject = detail.project || found;
                    activeProject.membership = detail.membership;
                }
            }
            updateProjectWorkspace();
        } catch (error) {
            console.error("Failed to load projects:", error);
        }
        if (currentUser?.id === userId) syncProjectEvents();
    }

    // Đóng kết nối SSE và xóa timer polling dự phòng
    // === KHỐI 10: Kết nối SSE và hàng đợi đồng bộ Project ===
    function stopProjectEvents() {
        projectEventsSource?.close();
        projectEventsSource = null;
        projectEventsUserId = "";
        clearInterval(projectEventsFallback);
        projectEventsFallback = null;
    }

    // Quản lý luồng SSE /api/projects/events; tự chuyển sang polling 10s khi SSE lỗi
    function syncProjectEvents() {
        // Ngắt kết nối nếu chưa đăng nhập hoặc tab đang bị ẩn để tiết kiệm tài nguyên
        if (!currentUser || document.hidden) { stopProjectEvents(); return; }
        const userId = String(currentUser.id);
        if (projectEventsUserId === userId) return; // Đang kết nối đúng user -> không mở lại
        stopProjectEvents();
        projectEventsUserId = userId;
        const fallback = () => {
            if (!currentUser || projectEventsUserId !== userId || document.hidden) return;
            // Khi SSE rớt/không hỗ trợ -> tải lại định kỳ mỗi 10 giây
            if (!projectEventsFallback) projectEventsFallback = setInterval(() => queueProjectRefresh(true), 10000);
        };
        if (typeof EventSource === "undefined") { fallback(); return; }
        const source = new EventSource("/api/projects/events");
        projectEventsSource = source;
        let membersRevision = "";
        source.addEventListener("projects_changed", event => {
            if (projectEventsSource !== source || String(currentUser?.id) !== userId) return;
            try {
                const update = JSON.parse(event.data);
                // So sánh members_revision để biết có cần reload luôn modal thành viên không
                const membersChanged = membersRevision !== update.members_revision;
                membersRevision = update.members_revision;
                queueProjectRefresh(membersChanged);
            } catch (error) { console.error("Invalid Project notification", error); }
        });
        source.addEventListener("session_expired", async () => {
            if (projectEventsSource !== source) return;
            stopProjectEvents();
            await checkAuthStatus();
            await loadProjects();
            if (!currentUser) await newChat();
        });
        source.addEventListener("retry_later", fallback);
        // SSE mở lại thành công -> tắt timer polling 10s
        source.onopen = () => { clearInterval(projectEventsFallback); projectEventsFallback = null; };
        source.onerror = fallback;
    }

    // Gom các yêu cầu refresh liên tiếp thành 1 hàng đợi tuần tự, tránh gọi API chồng chéo
    async function queueProjectRefresh(membersChanged = false) {
        projectRefreshPending = true;
        projectMembersRefreshPending ||= membersChanged;
        if (projectRefreshRunning) return;
        projectRefreshRunning = true;
        try {
            while (projectRefreshPending && currentUser) {
                projectRefreshPending = false;
                const refreshMembers = projectMembersRefreshPending;
                projectMembersRefreshPending = false;
                const previousProject = activeProjectId;
                await loadProjects();
                // Nếu đang mở một Project nhưng bị thu hồi quyền/xóa mất -> quay về chat mới
                if (previousProject && !activeProjectId) await newChat();
                await loadConversations();
                if (refreshMembers && projectMembersState) await refreshProjectMembersWhenSafe(projectMembersState);
            }
        } finally { projectRefreshRunning = false; }
    }

    // Không tự render đè modal thành viên nếu Owner đang chọn role hoặc xác nhận xóa
    async function refreshProjectMembersWhenSafe(state) {
        if (projectMembersState !== state) return;
        const list = document.getElementById("projectMembersList");
        if (state.busy || !state.loaded || list.querySelector('[data-save-role]:not(:disabled), .project-member-confirm')) {
            document.getElementById("projectMembersRefreshBtn").hidden = false;
            return;
        }
        await loadProjectMembers(state);
    }

    document.addEventListener("visibilitychange", () => {
        syncProjectEvents();
        if (!document.hidden && currentUser) queueProjectRefresh(true);
    });
    window.addEventListener("pagehide", stopProjectEvents);
    window.addEventListener("pageshow", () => { if (currentUser) syncProjectEvents(); });

    // === KHỐI 11: Chọn, tạo và sửa cài đặt Project ===
    async function selectProject(projectId) {
        activeProjectId = projectId;
        localStorage.setItem("active_project_id", projectId);
        const panel = document.getElementById("projectFilesPanel");
        if (panel) { panel.classList.remove("open"); panel.innerHTML = ""; }
        await loadProjects();
        await newChat();
    }

    /**
     * Thoát khỏi không gian làm việc của Project hiện tại để quay lại chế độ chat cá nhân.
     */
    async function exitProject() {
        activeProjectId = "";
        activeProject = null;
        localStorage.removeItem("active_project_id");
        const panel = document.getElementById("projectFilesPanel");
        if (panel) { panel.classList.remove("open"); panel.innerHTML = ""; }
        document.querySelectorAll(".project-list-item.active").forEach(el => el.classList.remove("active"));
        updateProjectWorkspace();
        await newChat();
    }

    async function createProjectUI() {
        if (!currentUser) { openAuthModal("signin"); return; }
        const name = prompt("Tên Project:");
        if (!name || !name.trim()) return;
        try {
            const response = await fetch("/api/projects", {
                method: "POST", headers: {"Content-Type":"application/json"},
                body: JSON.stringify({name: name.trim()})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Không thể tạo Project.");
            await loadProjects();
            await selectProject(data.project._id);
        } catch (error) { alert(error.message); }
    }

    async function editProjectSettings() {
        if (!activeProjectId || !activeProject) return;
        const membership = activeProject.membership || {};
        if (!["owner", "editor"].includes(membership.role)) { alert("Bạn chỉ có quyền trò chuyện trong Project này."); return; }
        const instructions = prompt("Project Instructions — áp dụng cho mọi chat trong Project:", activeProject.instructions || "");
        if (instructions === null) return;
        const name = prompt("Tên Project:", activeProject.name || "");
        if (name === null || !name.trim()) return;
        const description = prompt("Mô tả Project:", activeProject.description || "");
        if (description === null) return;
        const currentMode = activeProject.memory_mode || "project_only";
        const modeKeys = {project_only: "1", project_and_personal: "2", current_chat_only: "3"};
        const modeInput = prompt(
            "Chọn chế độ ngữ cảnh:\n1. Project-only: dùng ngữ cảnh các chat gần đây, file và memory của Project; không dùng memory cá nhân.\n2. Project + cá nhân: dùng ngữ cảnh Project và memory cá nhân của người đang chat.\n3. Chat hiện tại: chỉ dùng chat và file trong thread hiện tại; không nạp chat khác, Project memory hoặc memory cá nhân.",
            modeKeys[currentMode] || "1"
        );
        if (modeInput === null) return;
        const selectedMode = ({"1": "project_only", "2": "project_and_personal", "3": "current_chat_only"})[modeInput.trim()] || modeInput.trim();
        if (!["project_only", "project_and_personal", "current_chat_only"].includes(selectedMode)) {
            alert("Hãy nhập 1, 2 hoặc 3.");
            return;
        }
        const response = await fetch(`/api/projects/${activeProjectId}`, {
            method: "PATCH", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({name: name.trim(), description, instructions, memory_mode: selectedMode})
        });
        const data = await response.json();
        if (!response.ok) { alert(data.error || "Không thể lưu cài đặt."); return; }
        activeProject = {...activeProject, ...data.project, membership};
        updateProjectWorkspace();
        alert("Đã lưu Project Instructions và chế độ memory.");
    }

    // === KHỐI 12: Modal Share: thành viên, quyền và lời mời ===
    async function manageProjectMembers() {
        if (!activeProjectId || !activeProject) return;
        if (projectMembersState) return;
        const state = {
            projectId: activeProjectId,
            isOwner: activeProject.membership?.role === "owner",
            members: [], loaded: false, busy: false,
            returnFocus: document.activeElement
        };
        projectMembersState = state;
        document.getElementById("projectMembersProjectName").textContent = activeProject.name || "Project";
        document.getElementById("projectInviteForm").reset();
        document.getElementById("projectInvitationLinkBox").hidden = true;
        document.getElementById("projectMembersRefreshBtn").hidden = true;
        document.getElementById("projectInviteForm").hidden = !state.isOwner;
        document.getElementById("projectMembersReadOnly").hidden = state.isOwner;
        document.getElementById("projectMembersCount").textContent = "";
        setProjectMembersFeedback("");
        document.getElementById("projectMembersModal").style.display = "flex";
        document.addEventListener("keydown", handleProjectMembersKeydown, true);
        document.getElementById("projectMembersCloseBtn").focus();
        await loadProjectMembers(state);
    }

    function closeProjectMembersModal() {
        const state = projectMembersState;
        projectMembersState = null;
        document.getElementById("projectMembersModal").style.display = "none";
        document.removeEventListener("keydown", handleProjectMembersKeydown, true);
        if (state?.returnFocus?.isConnected && state.returnFocus.offsetParent !== null) state.returnFocus.focus();
    }

    function handleProjectMembersKeydown(event) {
        if (!projectMembersState) return;
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopImmediatePropagation();
            closeProjectMembersModal();
        } else if (event.key === "Tab") {
            const modal = document.getElementById("projectMembersModal");
            const items = [...modal.querySelectorAll("button, input, select, [tabindex='0']")]
                .filter(el => !el.matches(":disabled") && el.offsetParent !== null);
            const first = items[0], last = items[items.length - 1];
            if (!modal.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
            else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    }

    function setProjectMembersFeedback(message, kind = "success") {
        const notice = document.getElementById("projectMembersFeedback");
        notice.textContent = message;
        notice.dataset.kind = kind;
        notice.hidden = !message;
    }

    function projectMemberError(message) {
        return ({
            "Sign-in required.": "Vui lòng đăng nhập để quản lý thành viên.",
            "Only the project owner can invite members.": "Chỉ chủ sở hữu mới có thể mời thành viên.",
            "No registered account matches that email or username.": "Không tìm thấy tài khoản với email hoặc tên đăng nhập này.",
            "You already own this project.": "Bạn đã là chủ sở hữu của Project này.",
            "This user is already a project member.": "Người này đã là thành viên của Project.",
            "This account is inactive.": "Tài khoản này hiện đã bị vô hiệu hóa.",
            "Invitation has expired. Ask the owner to send a new invitation.": "Lời mời đã hết hạn. Hãy nhờ chủ sở hữu gửi lời mời mới.",
            "Invitation not found, revoked, already handled, or belongs to another account.": "Link không còn hiệu lực hoặc không thuộc tài khoản này. Hãy đăng nhập đúng tài khoản được mời hoặc nhờ chủ sở hữu gửi lại.",
            "Invitation not found, expired, or already handled.": "Lời mời đã hết hạn, bị thu hồi hoặc đã được xử lý.",
            "Project not found or access denied.": "Project không tồn tại hoặc bạn không còn quyền truy cập.",
            "Member or project not found, or only the owner may change roles.": "Không thể đổi quyền. Hãy kiểm tra quyền chủ sở hữu và trạng thái thành viên.",
            "Member not found, or only the owner may remove members.": "Không thể xóa thành viên. Hãy kiểm tra quyền chủ sở hữu và tải lại danh sách."
        })[message] || message || "Không thể thực hiện thao tác. Vui lòng thử lại.";
    }

    function updateProjectMembersBusy(state) {
        if (projectMembersState !== state) return;
        document.getElementById("projectInviteFields").disabled = !state.loaded || state.busy;
        document.querySelectorAll("#projectMembersList fieldset, #projectMembersList .project-members-retry")
            .forEach(el => { el.disabled = state.busy; });
        document.getElementById("projectInviteSubmitBtn").textContent = state.busy ? "Đang xử lý…" : "Gửi lời mời";
    }

    async function loadProjectMembers(state) {
        if (projectMembersState !== state) return;
        const list = document.getElementById("projectMembersList");
        state.loaded = false;
        document.getElementById("projectMembersRefreshBtn").hidden = true;
        list.setAttribute("aria-busy", "true");
        list.innerHTML = '<li class="project-members-empty">Đang tải thành viên…</li>';
        updateProjectMembersBusy(state);
        try {
            const response = await fetch(`/api/projects/${encodeURIComponent(state.projectId)}/members`);
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(projectMemberError(data.error));
            if (projectMembersState !== state) return;
            state.members = data.members || [];
            state.loaded = true;
            renderProjectMembers(state);
        } catch (error) {
            if (projectMembersState !== state) return;
            document.getElementById("projectMembersCount").textContent = "";
            list.innerHTML = '<li class="project-members-empty"><span></span><button type="button" class="project-tool-btn project-members-retry">Thử lại</button></li>';
            list.querySelector("span").textContent = `Không tải được danh sách thành viên. ${error.message}`;
            list.querySelector("button").onclick = () => loadProjectMembers(state);
        } finally {
            if (projectMembersState === state) {
                list.setAttribute("aria-busy", "false");
                updateProjectMembersBusy(state);
            }
        }
    }

    function renderProjectMembers(state) {
        const list = document.getElementById("projectMembersList");
        list.replaceChildren();
        const roleNames = {owner: "Chủ sở hữu", editor: "Có thể chỉnh sửa", chat_only: "Chỉ trò chuyện"};
        const statuses = {accepted: "Đã tham gia", invited: "Đang chờ chấp nhận", declined: "Đã từ chối", expired: "Đã hết hạn"};
        const accepted = state.members.filter(m => m.status === "accepted").length;
        const pending = state.members.filter(m => m.status === "invited").length;
        document.getElementById("projectMembersCount").textContent = `${accepted} đã tham gia${pending ? ` · ${pending} đang chờ` : ""}`;
        state.members.forEach(member => {
            const name = member.full_name || member.username || member.email || "Thành viên";
            const row = document.createElement("li");
            row.className = "project-member-row";
            row.innerHTML = `<div class="project-member-avatar" aria-hidden="true">${escapeHtml(Array.from(name)[0]?.toUpperCase() || "?")}</div>
                <div class="project-member-info">
                    <div class="project-member-name">${escapeHtml(name)}${String(member.user_id) === String(currentUser?.id) ? " (Bạn)" : ""}</div>
                    <div class="project-member-detail">${escapeHtml(member.email || (member.username ? `@${member.username}` : ""))}</div>
                    <div class="project-member-detail project-member-status" data-status="${escapeHtml(member.status)}">${escapeHtml(statuses[member.status] || member.status)}${member.status === "invited" && member.expires_at ? ` · Hết hạn ${escapeHtml(new Date(member.expires_at).toLocaleString("vi-VN"))}` : ""}</div>
                </div>`;
            const actions = document.createElement("fieldset");
            actions.className = "project-member-actions";
            actions.setAttribute("aria-label", `Quản lý ${name}`);
            actions.disabled = state.busy;
            if (state.isOwner && member.role !== "owner" && member.status === "accepted") {
                const select = document.createElement("select");
                select.setAttribute("aria-label", `Quyền của ${name}`);
                select.innerHTML = '<option value="chat_only">Chỉ trò chuyện</option><option value="editor">Có thể chỉnh sửa</option>';
                select.value = member.role;
                const save = document.createElement("button");
                save.type = "button";
                save.className = "project-tool-btn";
                save.textContent = "Lưu quyền";
                save.dataset.saveRole = "true";
                save.disabled = true;
                select.onchange = () => { save.disabled = select.value === member.role; };
                save.onclick = () => runProjectMemberAction(state, `/${encodeURIComponent(member.user_id)}`, "PATCH", {role: select.value}, `Đã cập nhật quyền của ${name}.`);
                actions.append(select, save);
            } else {
                const role = document.createElement("span");
                role.className = "project-member-role";
                role.textContent = roleNames[member.role] || member.role;
                actions.appendChild(role);
            }
            if (state.isOwner && member.role !== "owner") {
                if (["invited", "declined", "expired"].includes(member.status)) {
                    const reinvite = document.createElement("button");
                    reinvite.type = "button";
                    reinvite.className = "project-tool-btn";
                    reinvite.textContent = member.status === "invited" ? "Gửi lại" : "Mời lại";
                    reinvite.onclick = () => {
                        document.getElementById("projectInviteIdentifier").value = member.email || member.username || "";
                        document.getElementById(member.role === "editor" ? "projectInviteEditor" : "projectInviteChatOnly").checked = true;
                        document.getElementById("projectInviteIdentifier").focus();
                    };
                    actions.appendChild(reinvite);
                }
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "project-tool-btn project-member-remove";
                remove.textContent = member.status === "invited" ? "Thu hồi lời mời" : "Xóa";
                remove.onclick = () => confirmProjectMemberRemoval(state, member, row, remove);
                actions.appendChild(remove);
            }
            row.appendChild(actions);
            list.appendChild(row);
        });
        if (!state.members.length) list.innerHTML = '<li class="project-members-empty">Chưa có thành viên.</li>';
    }

    function confirmProjectMemberRemoval(state, member, row, trigger) {
        if (state.busy || row.querySelector(".project-member-confirm")) return;
        const name = member.full_name || member.username || member.email || "thành viên này";
        const pending = member.status === "invited";
        const declined = ["declined", "expired"].includes(member.status);
        const confirmation = document.createElement("fieldset");
        confirmation.className = "project-member-confirm";
        confirmation.innerHTML = '<p></p><button type="button" class="project-tool-btn">Hủy</button><button type="button" class="project-tool-btn project-member-remove"></button>';
        confirmation.querySelector("p").textContent = pending ? `Thu hồi lời mời gửi đến ${name}?`
            : declined ? `Xóa lời mời của ${name}?` : `Xóa ${name} khỏi Project? Người này sẽ mất quyền truy cập.`;
        const [cancel, confirm] = confirmation.querySelectorAll("button");
        cancel.onclick = () => { confirmation.remove(); trigger.focus(); };
        confirm.textContent = pending ? "Thu hồi" : declined ? "Xóa lời mời" : "Xóa thành viên";
        confirm.onclick = () => runProjectMemberAction(state, `/${encodeURIComponent(member.user_id)}`, "DELETE", null,
            pending ? `Đã thu hồi lời mời gửi đến ${name}.` : declined ? `Đã xóa lời mời của ${name}.` : `Đã xóa ${name} khỏi Project.`);
        row.appendChild(confirmation);
        cancel.focus();
    }

    async function submitProjectInvitation(event) {
        event.preventDefault();
        const state = projectMembersState;
        if (!state?.isOwner || state.busy || !state.loaded) return;
        const input = document.getElementById("projectInviteIdentifier");
        const identifier = input.value.trim();
        if (!identifier) {
            setProjectMembersFeedback("Vui lòng nhập email hoặc tên đăng nhập của người được mời.", "error");
            input.focus();
            return;
        }
        const role = document.querySelector('input[name="projectInviteRole"]:checked').value;
        const expiry_days = Number(document.getElementById("projectInviteDuration").value);
        await runProjectMemberAction(state, "", "POST", {identifier, role, expiry_days},
            "Đã tạo lời mời.", data => {
                document.getElementById("projectInviteForm").reset();
                document.getElementById("projectInvitationLinkValue").value = data.invitation_url;
                document.getElementById("projectInvitationLinkExpiry").textContent = `Hết hạn: ${new Date(data.invitation.expires_at).toLocaleString("vi-VN")}. Gửi lại sẽ vô hiệu hóa link cũ.`;
                document.getElementById("projectInvitationCopyBtn").textContent = "Sao chép";
                document.getElementById("projectInvitationLinkBox").hidden = false;
            });
    }

    async function copyProjectInvitationLink() {
        const input = document.getElementById("projectInvitationLinkValue");
        try {
            await navigator.clipboard.writeText(input.value);
            document.getElementById("projectInvitationCopyBtn").textContent = "Đã sao chép";
        } catch (error) {
            input.focus();
            input.select();
            setProjectMembersFeedback("Link đã được chọn. Nhấn Ctrl+C hoặc dùng thao tác sao chép của thiết bị.", "warning");
        }
    }

    async function runProjectMemberAction(state, suffix, method, body, successMessage, onSuccess) {
        if (projectMembersState !== state || !state.isOwner || state.busy || !state.loaded) return;
        state.busy = true;
        document.getElementById("projectInvitationLinkBox").hidden = true;
        setProjectMembersFeedback("");
        updateProjectMembersBusy(state);
        try {
            const options = {method};
            if (body) {
                options.headers = {"Content-Type": "application/json"};
                options.body = JSON.stringify(body);
            }
            const response = await fetch(`/api/projects/${encodeURIComponent(state.projectId)}/members${suffix}`, options);
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(projectMemberError(data.error));
            if (projectMembersState !== state) return;
            if (onSuccess) onSuccess(data);
            if (method === "POST") {
                setProjectMembersFeedback(data.email_sent
                    ? "Đã gửi lời mời qua email và trong ứng dụng. Bạn cũng có thể sao chép link bên dưới."
                    : "Đã tạo lời mời trong ứng dụng nhưng chưa gửi được email. Bạn có thể sao chép link bên dưới để gửi cho người nhận.", data.email_sent ? "success" : "warning");
            } else setProjectMembersFeedback(successMessage);
            await loadProjectMembers(state);
        } catch (error) {
            if (projectMembersState === state) setProjectMembersFeedback(error.message || "Không thể kết nối. Vui lòng thử lại.", "error");
        } finally {
            if (projectMembersState === state) {
                state.busy = false;
                updateProjectMembersBusy(state);
            }
        }
    }

    // === KHỐI 13: Xóa Project và xử lý lời mời ở sidebar ===
    async function deleteActiveProject() {
        if (!activeProjectId || activeProject?.membership?.role !== "owner") return;
        if (!confirm(`Xóa Project "${activeProject.name}" cùng toàn bộ chat, file và Project memory?`)) return;
        const response = await fetch(`/api/projects/${activeProjectId}`, {method:"DELETE"});
        const data = await response.json();
        if (!response.ok) { alert(data.error || "Không xóa được Project."); return; }
        activeProjectId = "";
        activeProject = null;
        localStorage.removeItem("active_project_id");
        const panel = document.getElementById("projectFilesPanel");
        if (panel) { panel.classList.remove("open"); panel.innerHTML = ""; }
        await newChat();
        await loadProjects();
    }

    async function respondProjectInvite(invitationId, accept, row) {
        const action = accept ? "accept" : "decline";
        row?.querySelectorAll("button").forEach(button => { button.disabled = true; });
        try {
            const response = await fetch(`/api/projects/invitations/${encodeURIComponent(invitationId)}/${action}`, {method:"POST"});
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(projectMemberError(data.error));
            await queueProjectRefresh(true);
        } catch (error) {
            if (row) {
                let message = row.querySelector('[role="status"]');
                if (!message) { message = document.createElement("div"); message.setAttribute("role", "status"); row.appendChild(message); }
                message.textContent = error.message || "Không thể xử lý lời mời. Vui lòng thử lại.";
            }
        } finally { row?.querySelectorAll("button").forEach(button => { button.disabled = false; }); }
    }

    // === KHỐI 14: Mở link mời và tiếp tục sau đăng nhập ===
    function captureProjectInvitationLink() {
        const params = new URLSearchParams(location.hash.slice(1));
        const token = params.get("project-invite");
        if (!token) return;
        pendingProjectInvitationToken = token;
        sessionStorage.setItem("pending_project_invitation", token);
        params.delete("project-invite");
        history.replaceState(null, "", location.pathname + location.search + (params.size ? `#${params}` : ""));
    }

    async function showProjectInvitationLink() {
        if (!pendingProjectInvitationToken || projectInvitationState) return;
        const state = {token: pendingProjectInvitationToken, userId: currentUser?.id, busy: false, returnFocus: document.activeElement};
        projectInvitationState = state;
        document.getElementById("projectInvitationModal").style.display = "flex";
        document.getElementById("projectInvitationActions").hidden = true;
        document.getElementById("projectInvitationSignInBtn").hidden = Boolean(currentUser);
        const description = document.getElementById("projectInvitationDescription");
        const errorBox = document.getElementById("projectInvitationError");
        errorBox.hidden = true;
        document.getElementById("projectInvitationCloseBtn").focus();
        document.addEventListener("keydown", handleProjectInvitationKeydown, true);
        if (!currentUser) {
            description.textContent = "Đăng nhập đúng tài khoản nhận lời mời để xem và tham gia Project.";
            return;
        }
        description.textContent = "Đang kiểm tra lời mời…";
        try {
            const response = await fetch("/api/projects/invitations/preview", {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: state.token})
            });
            const data = await response.json();
            if (projectInvitationState !== state) return;
            if (!response.ok || !data.success) throw new Error(projectMemberError(data.error));
            const invite = data.invitation;
            state.invitation = invite;
            description.textContent = `${invite.inviter_name} mời bạn tham gia “${invite.project_name}”. Quyền: ${invite.role === "editor" ? "Có thể chỉnh sửa" : "Chỉ trò chuyện"}. Hết hạn: ${new Date(invite.expires_at).toLocaleString("vi-VN")}.`;
            document.getElementById("projectInvitationActions").hidden = false;
            document.querySelectorAll("#projectInvitationActions button").forEach(button => { button.disabled = false; });
        } catch (error) {
            if (projectInvitationState !== state) return;
            description.textContent = "Không thể mở lời mời này.";
            errorBox.textContent = error.message || "Không thể kết nối. Hãy thử mở lại link.";
            errorBox.hidden = false;
        }
    }

    function closeProjectInvitationModal(clearToken = true) {
        const state = projectInvitationState;
        projectInvitationState = null;
        document.getElementById("projectInvitationModal").style.display = "none";
        document.removeEventListener("keydown", handleProjectInvitationKeydown, true);
        if (clearToken) { pendingProjectInvitationToken = ""; sessionStorage.removeItem("pending_project_invitation"); }
        if (state?.returnFocus?.isConnected) state.returnFocus.focus();
    }

    function handleProjectInvitationKeydown(event) {
        if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); closeProjectInvitationModal(); }
        else if (event.key === "Tab") {
            const modal = document.getElementById("projectInvitationModal");
            const buttons = [...modal.querySelectorAll("button")].filter(button => !button.disabled && button.offsetParent !== null);
            const first = buttons[0], last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!modal.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)) { event.preventDefault(); first.focus(); }
        }
    }

    async function respondProjectInvitationLink(accept) {
        const state = projectInvitationState;
        if (!state?.invitation || state.busy || currentUser?.id !== state.userId) return;
        state.busy = true;
        document.querySelectorAll("#projectInvitationActions button").forEach(button => { button.disabled = true; });
        const errorBox = document.getElementById("projectInvitationError");
        errorBox.hidden = true;
        try {
            const response = await fetch("/api/projects/invitations/respond", {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: state.token, accept})
            });
            const data = await response.json();
            if (projectInvitationState !== state) return;
            if (!response.ok || !data.success) throw new Error(projectMemberError(data.error));
            closeProjectInvitationModal();
            await queueProjectRefresh(true);
            if (accept) await selectProject(data.project_id);
        } catch (error) {
            if (projectInvitationState === state) { errorBox.textContent = error.message; errorBox.hidden = false; }
        } finally {
            if (projectInvitationState === state) {
                state.busy = false;
                document.querySelectorAll("#projectInvitationActions button").forEach(button => { button.disabled = false; });
            }
        }
    }

    // === KHỐI 15: Danh sách và thao tác tài liệu Project ===
    async function toggleProjectFiles() {
        const panel = document.getElementById("projectFilesPanel");
        if (!panel || !activeProjectId) return;
        panel.classList.toggle("open");
        if (!panel.classList.contains("open")) return;
        const response = await fetch(`/api/projects/${activeProjectId}/files`);
        const data = await response.json();
        panel.innerHTML = `<div style="font-size:12px;color:var(--text-muted);margin-bottom:5px">File tải lên từ chat Project sẽ dùng được trong mọi chat của Project.</div>`;
        (data.files || []).forEach(file => {
            const row = document.createElement("div");
            row.className = "project-file-row";
            row.innerHTML = `<span>${escapeHtml(file.filename)}</span><a class="project-tool-btn" href="/api/projects/${activeProjectId}/files/${encodeURIComponent(file.document_id)}/download">Tải xuống</a>`;
            if (["owner", "editor"].includes(activeProject?.membership?.role)) {
                const del = document.createElement("button");
                del.className = "project-tool-btn"; del.textContent = "Xóa";
                del.onclick = () => deleteProjectFile(file.document_id);
                row.appendChild(del);
            }
            panel.appendChild(row);
        });
        if (!(data.files || []).length) panel.insertAdjacentHTML("beforeend", `<div style="font-size:12px;color:var(--text-muted)">Chưa có file.</div>`);
    }

    async function deleteProjectFile(documentId) {
        if (!confirm("Xóa file này khỏi Project?")) return;
        const response = await fetch(`/api/projects/${activeProjectId}/files/${encodeURIComponent(documentId)}`, {method:"DELETE"});
        const data = await response.json();
        if (!response.ok) alert(data.error || "Không xóa được file.");
        const panel = document.getElementById("projectFilesPanel");
        if (panel) { panel.classList.remove("open"); await toggleProjectFiles(); }
    }

    /**
     * Kiểm tra user hiện tại có quyền sửa/xóa một cuộc trò chuyện cụ thể trong Project hay không.
     * Chỉ người tạo chat, Project owner hoặc Project editor mới có quyền thao tác.
     */
    // === KHỐI 16: Danh sách chat, lịch sử và chuyển chat vào Project ===
    function canManageProjectConversationItem(conv) {
        if (!conv || !activeProjectId) return true;
        if (currentUser && String(conv.user_id || "") === String(currentUser.id || "")) return true;
        const role = activeProject?.membership?.role || "";
        return role === "owner" || role === "editor";
    }

    /**
     * Dựng một phần tử DOM cho từng cuộc trò chuyện theo phạm vi ('personal' hoặc 'project').
     */
    function renderConversationItem(conv, scope) {
        const item = document.createElement("div");
        item.className = "history-item" + (conv.is_pinned ? " pinned" : "");

        if (conv.thread_id === threadId) {
            item.classList.add("active");
        }

        const itemIcon = conv.is_pinned
            ? `<svg class="history-item-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5"><line x1="12" y1="17" x2="12" y2="22"></line><path d="M5 17h14v-2l-1.8-1.8a2 2 0 0 1-.6-1.4V6h1V4H6v2h1v5.8a2 2 0 0 1-.6 1.4L5 15v2z"></path></svg>`
            : `<svg class="history-item-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;

        const addProjectIconSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path><line x1="12" y1="11" x2="12" y2="17"></line><line x1="9" y1="14" x2="15" y2="14"></line></svg>`;
        const pinIconSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="${conv.is_pinned ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"></line><path d="M5 17h14v-2l-1.8-1.8a2 2 0 0 1-.6-1.4V6h1V4H6v2h1v5.8a2 2 0 0 1-.6 1.4L5 15v2z"></path></svg>`;
        const dotsIconSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="2"></circle><circle cx="12" cy="5" r="2"></circle><circle cx="12" cy="19" r="2"></circle></svg>`;
        const trashIconSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;

        const canManage = scope === "personal" || canManageProjectConversationItem(conv);
        const addProjectBtnHtml = scope === "personal"
            ? `<button class="history-action-btn add-project-btn" title="Thêm vào Project" aria-label="Thêm vào Project">${addProjectIconSvg}</button>`
            : "";
        const manageBtnsHtml = canManage
            ? `<button class="history-action-btn pin-btn ${conv.is_pinned ? 'is-pinned' : ''}" title="${conv.is_pinned ? 'Bỏ ghim' : 'Ghim lên đầu'}" aria-label="${conv.is_pinned ? 'Bỏ ghim' : 'Ghim'}">${pinIconSvg}</button>
               <button class="history-action-btn rename-btn" title="Đổi tên cuộc trò chuyện" aria-label="Đổi tên">${dotsIconSvg}</button>
               <button class="history-action-btn delete-btn" title="Xóa cuộc trò chuyện" aria-label="Xóa">${trashIconSvg}</button>`
            : "";

        item.innerHTML = `
            ${itemIcon}
            <span class="history-item-title" title="${escapeHtml(conv.title || 'Đoạn chat mới')}">${escapeHtml(conv.title || "Đoạn chat mới")}</span>
            <div class="history-item-actions">${addProjectBtnHtml}${manageBtnsHtml}</div>
        `;

        // Mở chat cá nhân sẽ truyền selectedProjectId="" để thoát chế độ Project một cách tự nhiên.
        item.onclick = () => loadConversation(conv.thread_id, scope === "project" ? (activeProjectId || "") : "");

        const addProjBtn = item.querySelector(".add-project-btn");
        if (addProjBtn) {
            addProjBtn.onclick = (e) => {
                e.stopPropagation();
                openAddToProjectModal(conv.thread_id, conv.title || "Đoạn chat mới");
            };
        }

        const pinBtn = item.querySelector(".pin-btn");
        if (pinBtn) {
            pinBtn.onclick = (e) => {
                e.stopPropagation();
                togglePinConversation(conv.thread_id);
            };
        }

        const renameBtn = item.querySelector(".rename-btn");
        if (renameBtn) {
            renameBtn.onclick = (e) => {
                e.stopPropagation();
                renameConversationPrompt(conv.thread_id, conv.title || "Đoạn chat mới");
            };
        }

        const delBtn = item.querySelector(".delete-btn");
        if (delBtn) {
            delBtn.onclick = (e) => {
                e.stopPropagation();
                deleteConversationPrompt(conv.thread_id);
            };
        }

        return item;
    }

    /**
     * Render danh sách các cuộc trò chuyện (chia nhóm Đã ghim / Gần đây) vào một container DOM.
     */
    function renderConversationList(container, items, scope, emptyText = "Chưa có cuộc trò chuyện nào") {
        if (!container) return;
        container.innerHTML = "";
        if (!items || items.length === 0) {
            container.innerHTML = `<div class="history-item" style="color: var(--text-muted); cursor: default; justify-content: center;">${escapeHtml(emptyText)}</div>`;
            return;
        }

        const pinned = items.filter(c => c.is_pinned);
        const recent = items.filter(c => !c.is_pinned);

        if (pinned.length > 0) {
            const pinnedHeader = document.createElement("div");
            pinnedHeader.className = "history-section-header";
            pinnedHeader.innerHTML = `
                <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5"><line x1="12" y1="17" x2="12" y2="22"></line><path d="M5 17h14v-2l-1.8-1.8a2 2 0 0 1-.6-1.4V6h1V4H6v2h1v5.8a2 2 0 0 1-.6 1.4L5 15v2z"></path></svg>
                <span>Đã ghim</span>
            `;
            container.appendChild(pinnedHeader);
            pinned.forEach(c => container.appendChild(renderConversationItem(c, scope)));
        }

        if (recent.length > 0) {
            if (pinned.length > 0) {
                const recentHeader = document.createElement("div");
                recentHeader.className = "history-section-header";
                recentHeader.innerHTML = `<span>Gần đây</span>`;
                container.appendChild(recentHeader);
            }
            recent.forEach(c => container.appendChild(renderConversationItem(c, scope)));
        }
    }

    /**
     * Tải danh sách chat cá nhân (luôn giữ hiển thị ở sidebar) và danh sách chat Project nếu đang mở Project.
     */
    async function loadConversations() {
        const projectListEl = document.getElementById("projectConversationList");
        const personalTitleEl = document.getElementById("personalChatSectionTitle");
        if (personalTitleEl) personalTitleEl.style.display = currentUser ? "flex" : "none";

        try {
            const requests = [fetch("/api/conversations").then(r => r.json())];
            if (activeProjectId) {
                requests.push(fetch(`/api/projects/${activeProjectId}/conversations`).then(r => r.json()));
            }
            const [personalData, projectData] = await Promise.all(requests);

            renderConversationList(
                conversationList,
                personalData?.conversations || [],
                "personal",
                "Chưa có cuộc trò chuyện cá nhân"
            );

            if (activeProjectId && projectListEl) {
                renderConversationList(
                    projectListEl,
                    projectData?.conversations || [],
                    "project",
                    "Chưa có chat trong Project"
                );
            } else if (projectListEl) {
                projectListEl.innerHTML = "";
            }
        } catch (error) {
            console.error("Failed to load conversations:", error);
        }
    }

    /**
     * Mở modal chọn Project đích để thêm một cuộc trò chuyện cá nhân vào Project (chỉ owner/editor).
     */
    function openAddToProjectModal(targetThreadId, title) {
        if (!currentUser) { openAuthModal("signin"); return; }
        const editableProjects = (cachedProjects || []).filter(
            p => ["owner", "editor"].includes(p?.membership?.role)
        );
        if (editableProjects.length === 0) {
            alert("Bạn cần là owner hoặc editor của ít nhất một Project để thêm cuộc trò chuyện.");
            return;
        }

        pendingAddThreadId = targetThreadId;
        const modal = document.getElementById("addToProjectModal");
        const desc = document.getElementById("addToProjectConvDesc");
        const select = document.getElementById("targetProjectSelect");
        const err = document.getElementById("addToProjectError");
        if (!modal || !select) return;

        if (err) { err.style.display = "none"; err.textContent = ""; }
        if (desc) desc.textContent = `Cuộc trò chuyện: "${title || "Đoạn chat mới"}"`;
        select.innerHTML = "";
        editableProjects.forEach(proj => {
            const opt = document.createElement("option");
            opt.value = proj._id;
            opt.textContent = `${proj.name} (${proj.membership.role})`;
            if (activeProjectId && proj._id === activeProjectId) {
                opt.selected = true;
            }
            select.appendChild(opt);
        });
        modal.style.display = "flex";
    }

    /**
     * Đóng modal Add to Project và reset trạng thái thread đang chờ.
     */
    function closeAddToProjectModal() {
        const modal = document.getElementById("addToProjectModal");
        if (modal) modal.style.display = "none";
        pendingAddThreadId = "";
    }

    /**
     * Gửi yêu cầu đưa cuộc trò chuyện cá nhân vào Project đích và cập nhật lại giao diện.
     */
    async function submitAddToProject(event) {
        event.preventDefault();
        if (!pendingAddThreadId) return;

        const select = document.getElementById("targetProjectSelect");
        const err = document.getElementById("addToProjectError");
        const btn = document.getElementById("addToProjectSubmitBtn");
        const targetProjectId = select ? select.value : "";
        if (!targetProjectId) return;

        if (btn) btn.disabled = true;
        if (err) { err.style.display = "none"; err.textContent = ""; }

        try {
            const response = await fetch(`/api/conversations/${encodeURIComponent(pendingAddThreadId)}/project`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: targetProjectId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || "Không thể thêm cuộc trò chuyện vào Project.");
            }

            const movedThreadId = pendingAddThreadId;
            closeAddToProjectModal();

            // Nếu đang mở chính cuộc trò chuyện đó hoặc chưa ở trong Project nào, mở thẳng nó trong Project đích.
            if (threadId === movedThreadId || !activeProjectId) {
                await loadConversation(movedThreadId, targetProjectId);
            } else {
                await loadConversations();
                const filesPanel = document.getElementById("projectFilesPanel");
                if (activeProjectId === targetProjectId && filesPanel && filesPanel.classList.contains("open")) {
                    filesPanel.classList.remove("open");
                    await toggleProjectFiles();
                }
            }
        } catch (error) {
            if (err) {
                err.textContent = error.message || "Không thể thêm cuộc trò chuyện vào Project.";
                err.style.display = "block";
            } else {
                alert(error.message);
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function togglePinConversation(id) {
        try {
            const res = await fetch(`/api/conversations/${id}/pin`, {
                method: "POST",
                headers: { "Content-Type": "application/json" }
            });
            const data = await res.json();
            if (res.ok && data.success) {
                await loadConversations();
            } else {
                alert(data.error || "Không thể thay đổi trạng thái ghim.");
            }
        } catch (err) {
            console.error("Failed to toggle pin conversation:", err);
        }
    }

    async function deleteConversationPrompt(id) {
        if (!confirm("Bạn có chắc chắn muốn xóa cuộc trò chuyện này?")) return;
        try {
            const res = await fetch(`/api/conversations/${id}`, {
                method: "DELETE"
            });
            if (res.ok) {
                if (threadId === id) {
                    threadId = crypto.randomUUID();
                    localStorage.setItem("thread_id", threadId);
                    chatContainer.innerHTML = "";
                    renderWelcome();
                }
                await loadConversations();
            } else {
                const data = await res.json().catch(() => ({}));
                alert(data.error || "Không thể xóa cuộc trò chuyện.");
            }
        } catch (err) {
            console.error("Failed to delete conversation:", err);
        }
    }

    async function renameConversationPrompt(id, currentTitle) {
        const newTitle = prompt("Nhập tên mới cho cuộc trò chuyện:", currentTitle);
        if (!newTitle || !newTitle.trim() || newTitle.trim() === currentTitle) return;
        try {
            const res = await fetch(`/api/conversations/${id}/title`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: newTitle.trim() })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.success) {
                await loadConversations();
            } else {
                alert(data.error || "Không thể đổi tên cuộc trò chuyện.");
            }
        } catch (err) {
            console.error("Failed to rename conversation:", err);
            alert("Lỗi kết nối khi đổi tên cuộc trò chuyện.");
        }
    }

    /**
     * Tải lịch sử một cuộc trò chuyện và đồng bộ ngữ cảnh Project/Personal tương ứng.
     */
    async function loadConversation(selectedThreadId, selectedProjectId = "", { skipProjectReload = false } = {}) {
        clearPendingAttachment();
        isTemporaryChat = false;
        activeProjectId = selectedProjectId || "";
        if (activeProjectId) {
            localStorage.setItem("active_project_id", activeProjectId);
        } else {
            localStorage.removeItem("active_project_id");
            const panel = document.getElementById("projectFilesPanel");
            if (panel) { panel.classList.remove("open"); panel.innerHTML = ""; }
            document.querySelectorAll(".project-list-item.active").forEach(el => el.classList.remove("active"));
        }
        if (!skipProjectReload) {
            activeProject = null;
            if (activeProjectId) await loadProjects();
        } else if (!activeProjectId) {
            activeProject = null;
        }
        updateProjectWorkspace();
        threadId = selectedThreadId;
        localStorage.setItem("thread_id", threadId);

        // Đóng sidebar trên mobile sau khi chọn đoạn chat
        if (window.innerWidth <= 768) {
            const sidebar = document.querySelector(".sidebar");
            if (sidebar) sidebar.classList.remove("open");
        }

        try {
            const response = await fetch(`/api/history/${threadId}`);
            if (!response.ok) {
                threadId = crypto.randomUUID();
                localStorage.setItem("thread_id", threadId);
                renderWelcome();
                await loadConversations();
                return;
            }
            const data = await response.json();

            if (!data.messages || data.messages.length === 0) {
                renderWelcome();
                await loadConversations();
                return;
            }

            chatContainer.innerHTML = "";

            data.messages.forEach(msg => {
                addMessage(
                    msg.role === "user" ? "user" : "assistant",
                    msg.content,
                    activeProjectId && msg.role === "user" ? (msg.sender_name || "Project member") : ""
                );
            });

            updateTemporaryChatVisibility();
            await loadConversations();

        } catch (error) {
            console.error("Failed to load conversation:", error);
            renderWelcome();
        }
    }

    // ---- Homepage (welcome hero + suggestion cards) ----
    const WELCOME_TITLE = "Xin chào, hôm nay tôi có thể giúp gì cho bạn?";

    const TIT_LOGO = `<svg class="tit-logo" viewBox="0 0 64 64" fill="none" aria-hidden="true"><g class="tit-tuft"><path d="M27.6 13 L31.2 11.4 L30.2 14.6 Z" fill="#0b0607" stroke="#0b0607" stroke-width="1.2" stroke-linejoin="round"/></g>
<path d="M33.5 47.5 L31.5 54.5 L26.5 57.6 M31.5 54.5 L29.6 59.2 M31.5 54.5 L34.6 58.6 M38 49 L36.6 55 L32 58.6 M36.6 55 L35.2 59.6" stroke="#0b0607" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
<path d="M6.2 18.6 C9.2 15.4 14 13.8 17.5 13.2 C19.5 10.8 24.5 9.6 28.5 12.5 C34 16 41 24 47 33 C50 37.5 53 44 57.5 53 C58.3 56 58 58.5 57 59.3 C53 58 46 55 41 51.5 L36 49.5 C30 48 24.5 42 22 34 C20.5 29 19.6 25 18.8 22 C17.5 20.2 12.5 19.8 6.2 18.6 Z" fill="#0b0607" stroke="#fff" stroke-opacity=".38" stroke-width=".7" stroke-linejoin="round"/>
<path d="M7.6 18.3 C11.4 17.8 14.8 16.6 17.4 15.4" stroke="#fff" stroke-opacity=".4" stroke-width=".6" stroke-linecap="round" fill="none"/>
<path d="M45 49.4 L56.6 58 M48.2 46.8 L57.6 55.4" stroke="#fff" stroke-opacity=".5" stroke-width=".7" stroke-linecap="round" fill="none"/>
<g class="tit-wing">
<path d="M21.6 21.5 C22.2 18.2 27.5 18.6 33.5 23.4 C41 29.6 49.5 38.4 56.2 52.4 C49.5 49.4 42 45.4 35 41.2 C28 37 21.4 29.8 21.6 21.5 Z" fill="#0b0607" stroke="#fff" stroke-opacity=".8" stroke-width=".9" stroke-linejoin="round"/>
<path d="M23.6 23.8 C24.2 21.2 28 21.6 33 25.8 C40 31.6 47.6 39.8 53.6 50" stroke="#fff" stroke-opacity=".38" stroke-width=".65" fill="none"/>
<path d="M37 37 C43 41.5 48.5 47 52.6 53.2 M34.2 38.8 C40 43.4 45 48.6 48.6 54.4 M31.5 40.2 C36.6 44.2 41 49 44 54.6" stroke="#fff" stroke-opacity=".65" stroke-width=".75" stroke-linecap="round" fill="none"/>
<path d="M27 31 C29.4 31.2 31.4 32.8 32.4 35 M30.6 28.4 C33 28.8 35 30.6 36 32.8 M34 26.4 C36.4 27 38.2 28.8 39.2 31 M25.6 34.4 C27.6 34.6 29.4 36 30.2 38" stroke="#fff" stroke-opacity=".6" stroke-width=".75" stroke-linecap="round" fill="none"/>
</g>
<g class="tit-eyeball"><circle class="tit-sclera" cx="23.6" cy="15.7" r="2.4" fill="#fff"/><g class="tit-pupil"><circle cx="23.2" cy="15.7" r="1.05" fill="#0b0607"/></g></g></svg>`;

    const ICON = {
        logo: TIT_LOGO,
        globe: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`,
        file: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`,
        eye: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`,
        bolt: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>`,
        brain: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 0 7 4.5v0A2.5 2.5 0 0 0 4.5 7v0A2.5 2.5 0 0 0 3 9.5v5A2.5 2.5 0 0 0 5.5 17v0A2.5 2.5 0 0 0 8 19.5v0A2.5 2.5 0 0 0 12 21V3a2.5 2.5 0 0 0-2.5-1z"></path><path d="M14.5 2A2.5 2.5 0 0 1 17 4.5v0A2.5 2.5 0 0 1 19.5 7v0A2.5 2.5 0 0 1 21 9.5v5a2.5 2.5 0 0 1-2.5 2.5v0a2.5 2.5 0 0 1-2.5 2.5v0A2.5 2.5 0 0 1 12 21"></path></svg>`,
        arrow: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>`
    };

    // === KHỐI 17: Màn hình chào và các câu hỏi mẫu ===
    function welcomeMarkup() {
        if (isTemporaryChat) {
            return `
                <div class="welcome temporary-mode" id="welcome">
                    <div class="welcome-aura"></div>
                    <div class="temporary-hero-icon" aria-hidden="true">
                        <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2.5c-3.1 0-5.1 1.2-6.1 3.8l-3.3 1.2c-.4.1-.6.5-.6.9 0 .6.4 1 1 1h18c.6 0 1-.4 1-1 0-.4-.2-.8-.6-.9l-3.3-1.2c-1-2.6-3-3.8-6.1-3.8zm-4.2 4c.6-1.6 1.9-2.3 4.2-2.3s3.6.7 4.2 2.3H7.8z"/>
                            <path d="M5.5 12.5a3.5 3.5 0 0 0-3.5 3.5c0 1.9 1.6 3.5 3.5 3.5 1.6 0 3-1.1 3.4-2.6.4-.1.7-.1 1.1-.1.4 0 .7 0 1.1.1.4 1.5 1.8 2.6 3.4 2.6 1.9 0 3.5-1.6 3.5-3.5a3.5 3.5 0 0 0-3.5-3.5c-1.5 0-2.8.9-3.3 2.2-.4 0-.8-.1-1.2-.1s-.8.1-1.2.1c-.5-1.3-1.8-2.2-3.3-2.2z"/>
                        </svg>
                    </div>
                    <h1 class="welcome-title temp-title">Temporary chat</h1>
                    <p class="welcome-subtitle temp-subtitle">
                        This chat can reference memory, plugins, and custom instructions, but it won't appear in your history
                    </p>
                </div>
            `;
        }

        const titleHtml = WELCOME_TITLE
            .split(" ")
            .map((word, i) => `<span class="w" style="--i:${i}">${escapeHtml(word)}</span>`)
            .join(" ");

        const pills = [
            ["globe", "Web Search"],
            ["file", "Map-Reduce RAG"],
            ["eye", "Vision OCR"],
            ["bolt", "Precise Tools"],
            ["brain", "Memory"]
        ].map(([icon, label], i) =>
            `<span class="welcome-pill" style="--i:${i}"><span class="pill-icon">${ICON[icon]}</span>${label}</span>`
        ).join("");

        const card = (cls, prompt, icon, tag, title, desc, hint) => `
            <div class="card ${cls}" role="button" tabindex="0" data-prompt="${escapeHtml(prompt)}">
                <div class="card-glow"></div>
                <div class="card-header">
                    <div class="card-icon">${icon}</div>
                    <span class="card-tag">${tag}</span>
                </div>
                <div class="card-body">
                    <div class="card-title">${title}</div>
                    <div class="card-desc">${desc}</div>
                </div>
                <div class="card-footer">
                    <span class="card-prompt-hint">${hint}</span>
                    <span class="card-arrow">${ICON.arrow}</span>
                </div>
            </div>`;

        const webIcon = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`;
        const docIcon = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>`;
        const visionIcon = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>`;

        return `
            <div class="welcome" id="welcome">
                <div class="welcome-aura"></div>
                <div class="orbit">
                    <span class="orbit-ring r1" aria-hidden="true"></span>
                    <span class="orbit-ring r2" aria-hidden="true"></span>
                    <span class="orbit-ring r3" aria-hidden="true"></span>
                    <div class="orbit-core" role="button" tabindex="0" aria-label="Logo Tit, bấm để chào">${ICON.logo}</div>
                    <div class="tit-bubble" role="status" aria-live="polite"></div>
                </div>
                <div class="welcome-badge">
                    <span class="welcome-badge-dot"></span>
                    <span>Tit 3.3 • Intelligent Conversational Agent</span>
                </div>
                <h1 class="welcome-title" aria-label="${escapeHtml(WELCOME_TITLE)}">${titleHtml}</h1>
                <p class="welcome-subtitle">
                    Trợ lý AI đa năng hỗ trợ tìm kiếm web thời gian thực, đọc hiểu tài liệu chuyên sâu và phân tích, miêu tả hình ảnh trực quan.
                </p>
                <div class="welcome-pills">${pills}</div>
            </div>

            <div class="cards" id="cards">
                ${card("card-web", "Tìm kiếm tin tức công nghệ AI Agent mới nhất hôm nay", webIcon, "Realtime Web", "Tra cứu & Khám phá Web", "Truy vấn tin tức thời sự, sự kiện công nghệ và dữ liệu trực tuyến mới nhất.", "\"Tìm kiếm tin tức AI Agent mới nhất...\"")}
                ${card("card-doc", "Tóm tắt tài liệu đính kèm và nêu các luận điểm cốt lõi", docIcon, "Document RAG", "Phân tích & Tóm tắt Tài liệu", "Đọc hiểu PDF đa trang, trích xuất luận điểm chính và hỏi đáp chuyên sâu.", "\"Tóm tắt tài liệu đính kèm...\"")}
                ${card("card-vision", "Hãy miêu tả chi tiết bức ảnh này và cho biết có những đặc điểm gì nổi bật", visionIcon, "Vision & Image Analysis", "Phân tích & Miêu tả Ảnh", "Thị giác máy tính nhận diện vật thể, đọc chữ trong ảnh và miêu tả chi tiết ngữ cảnh.", "\"Hãy miêu tả chi tiết bức ảnh này...\"")}
            </div>
        `;
    }

    function renderWelcome() {
        chatContainer.innerHTML = welcomeMarkup();
        updateTemporaryChatVisibility();
        if (noticeText) {
            noticeText.textContent = isTemporaryChat
                ? "For safety, we may keep a copy of this chat for up to 30 days."
                : "Tit can make mistakes. Check important info.";
        }
        if (messageInput) {
            messageInput.placeholder = isTemporaryChat
                ? "Ask anything in temporary chat..."
                : "Ask anything, or attach a file and ask about it...";
        }
    }

    // Cards: click / keyboard to use the prompt, plus a cursor-following spotlight
    chatContainer.addEventListener("click", (e) => {
        const card = e.target.closest(".card[data-prompt]");
        if (card) usePrompt(card.dataset.prompt);
    });
    chatContainer.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        const card = e.target.closest(".card[data-prompt]");
        if (card) {
            e.preventDefault();
            usePrompt(card.dataset.prompt);
        }
    });
    chatContainer.addEventListener("pointermove", (e) => {
        const card = e.target.closest(".card");
        if (!card) return;
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--mx", (e.clientX - rect.left) + "px");
        card.style.setProperty("--my", (e.clientY - rect.top) + "px");
    });

    // === KHỐI 18: Gửi tin nhắn và đọc stream trả lời AI ===
    function parseSSEPart(part) {
        const lines = part
            .split(/\r?\n/)
            .filter(line => line.trim().startsWith("data:"));

        if (lines.length === 0) {
            return null;
        }

        const jsonText = lines
            .map(line => line.replace(/^data:\s*/, ""))
            .join("\n")
            .trim();

        if (!jsonText || jsonText === "[DONE]") {
            return null;
        }

        try {
            return JSON.parse(jsonText);
        } catch (error) {
            console.error("Invalid stream JSON:", jsonText, error);
            return null;
        }
    }

    async function sendMessage() {
        if (sendBtn.disabled) return;

        let message = messageInput.value.trim();
        const attachment = pendingFile;
        const targetThreadId = threadId;

        if (!message && !attachment) return;

        if (isDictating) {
            stopDictation();
        }

        const selectedModel = modelSelect ? modelSelect.value : "openai/gpt-oss-20b";
        let uploadResult = null;

        if (attachment) {
            sendBtn.disabled = true;
            statusText.textContent = "Uploading attachment...";
            const uploadProgress = addToolProgress("File Upload");

            try {
                const formData = new FormData();
                formData.append("file", attachment);
                formData.append("thread_id", targetThreadId);
                formData.append("is_temporary", isTemporaryChat ? "true" : "false");
                if (activeProjectId) formData.append("project_id", activeProjectId);

                const uploadResponse = await fetch("/api/upload", {
                    method: "POST",
                    body: formData
                });
                const uploadData = await uploadResponse.json();

                if (!uploadResponse.ok || !uploadData.success) {
                    throw new Error(uploadData.message || "Upload failed.");
                }

                uploadResult = uploadData;
                completeToolProgress(uploadProgress, "File Upload");
                clearPendingAttachment();
            } catch (error) {
                completeToolProgress(uploadProgress, "File Upload");
                addMessage("assistant", "Upload failed: " + error.message);
                sendBtn.disabled = false;
                statusText.textContent = "Ready";
                return;
            }
        }

        if (attachment && !message) {
            addMessage("user", `📎 ${attachment.name}`, activeProjectId ? (currentUser?.full_name || currentUser?.username || "") : "");
            addMessage("assistant", uploadResult.message);
            sendBtn.disabled = false;
            statusText.textContent = "Ready";
            if (!isTemporaryChat) {
                await loadConversations();
            }
            return;
        }

        const fileKind = attachment && isImageFile(attachment) ? "image" : "file";
        const submittedMessage = attachment
            ? `${message}\n\n[Attached ${fileKind}: ${attachment.name}]`
            : message;

        addMessage("user", attachment ? `${message}\n\n📎 ${attachment.name}` : message, activeProjectId ? (currentUser?.full_name || currentUser?.username || "") : "");

        messageInput.value = "";
        messageInput.style.height = "auto";

        sendBtn.disabled = true;
        statusText.textContent = `Thinking with ${selectedModel}...`;

        const botElement = addMessage("assistant", "");

        let firstTokenReceived = false;
        let thinkingCard = null;
        let thinkingBody = null;
        let thinkingLabel = null;
        let thinkingStartedAt = 0;
        let thinkingTimer = null;
        let thinkingFinalized = false;

        /**
         * Khởi tạo khối hiển thị quá trình suy luận live ngay phía trên tin nhắn của bot.
         */
        function ensureThinkingBox() {
            if (thinkingCard) return;
            const wrapper = document.createElement("div");
            wrapper.className = "thinking-box";

            thinkingCard = document.createElement("details");
            thinkingCard.className = "thinking-card";
            thinkingCard.open = true;

            const summary = document.createElement("summary");
            summary.className = "thinking-summary";

            const dot = document.createElement("span");
            dot.className = "thinking-dot";

            thinkingLabel = document.createElement("span");
            thinkingLabel.textContent = "Đang suy luận...";

            const chevron = document.createElement("span");
            chevron.className = "thinking-chevron";
            chevron.textContent = "▾";

            thinkingBody = document.createElement("div");
            thinkingBody.className = "thinking-body";

            summary.appendChild(dot);
            summary.appendChild(thinkingLabel);
            summary.appendChild(chevron);
            thinkingCard.appendChild(summary);
            thinkingCard.appendChild(thinkingBody);
            wrapper.appendChild(thinkingCard);

            if (botElement && botElement.parentElement) {
                botElement.parentElement.insertAdjacentElement("beforebegin", wrapper);
            }

            thinkingStartedAt = performance.now();
            thinkingTimer = setInterval(() => {
                if (thinkingFinalized || !thinkingLabel) return;
                const elapsed = ((performance.now() - thinkingStartedAt) / 1000).toFixed(1);
                thinkingLabel.textContent = `Đang suy luận... (${elapsed}s)`;
            }, 150);
        }

        /**
         * Đóng gọn khối suy luận ngay khi bot bắt đầu trả lời chính hoặc kết thúc stream.
         */
        function finalizeThinkingBox() {
            if (!thinkingCard || thinkingFinalized) return;
            thinkingFinalized = true;
            if (thinkingTimer) {
                clearInterval(thinkingTimer);
                thinkingTimer = null;
            }
            const elapsed = Math.max(0.1, (performance.now() - thinkingStartedAt) / 1000).toFixed(1);
            thinkingCard.classList.add("done");
            thinkingCard.open = false;
            if (thinkingLabel) {
                thinkingLabel.textContent = `Đã suy luận trong ${elapsed} giây`;
            }
        }

        function handleStreamData(data) {
            if (!data) return;

            if (Array.isArray(data.suggestions)) {
                addFollowupSuggestions(data.suggestions, botElement);
            }

            if (data.reasoning !== undefined && data.reasoning !== null && data.reasoning !== "") {
                ensureThinkingBox();
                if (!thinkingFinalized) {
                    statusText.textContent = `Reasoning with ${selectedModel}...`;
                }
                thinkingBody.textContent += data.reasoning;
                thinkingBody.scrollTop = thinkingBody.scrollHeight;
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            if (data.token !== undefined && data.token !== null) {
                if (!firstTokenReceived) {
                    firstTokenReceived = true;
                    finalizeThinkingBox();
                    statusText.textContent = `Generating with ${selectedModel}...`;
                }

                // Accumulate raw markdown text in a data attribute
                botElement.dataset.raw = (botElement.dataset.raw || "") + data.token;
                // Re-render markdown on every token (smooth streaming effect)
                renderMarkdown(botElement, botElement.dataset.raw);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            if (data.error) {
                finalizeThinkingBox();
                botElement.dataset.raw = (botElement.dataset.raw || "") + "\n\n**Error:** " + data.error;
                renderMarkdown(botElement, botElement.dataset.raw);
                statusText.textContent = "Ready";
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            if (data.done) {
                finalizeThinkingBox();
                statusText.textContent = "Ready";
            }
        }

        try {
            const response = await fetch("/api/chat/stream", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Language": navigator.language || "vi"
                },
                body: JSON.stringify({
                    message: submittedMessage,
                    thread_id: targetThreadId,
                    attachment_source: uploadResult?.source || null,
                    model: selectedModel,
                    is_temporary: isTemporaryChat,
                    project_id: activeProjectId || null
                })
            });

            if (!response.ok) {
                let errorText = "Request failed.";

                try {
                    const errorData = await response.json();
                    errorText = errorData.error || errorData.detail || errorData.message || errorText;
                } catch (error) {
                    console.error("Could not parse error response:", error);
                }

                botElement.textContent = errorText;
                return;
            }

            if (!response.body) {
                botElement.textContent = "Streaming is not supported by this browser.";
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");

            let buffer = "";

            while (true) {
                const { value, done } = await reader.read();

                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });

                const parts = buffer.split(/\r?\n\r?\n/);
                buffer = parts.pop() || "";

                for (const part of parts) {
                    const data = parseSSEPart(part);
                    handleStreamData(data);
                }
            }

            /*
                Important fix:
                Sometimes the final streamed response does not end with "\n\n".
                In that case, the final data remains inside buffer.
                This part makes sure the leftover buffer is also displayed.
            */
            buffer += decoder.decode();

            if (buffer.trim()) {
                const data = parseSSEPart(buffer);
                handleStreamData(data);
            }

        } catch (error) {
            console.error("Streaming error:", error);

            botElement.dataset.raw = "**Something went wrong:** " + error.message;
            renderMarkdown(botElement, botElement.dataset.raw);

        } finally {
            finalizeThinkingBox();
            sendBtn.disabled = false;
            statusText.textContent = "Ready";
            messageInput.focus();

            if (!isTemporaryChat) {
                await loadConversations();
            } else {
                localStorage.removeItem("thread_id");
                if (noticeText) {
                    noticeText.textContent = "For safety, we may keep a copy of this chat for up to 30 days.";
                }
            }

            // Kích hoạt luồng Follow-up chạy song song ngầm (người dùng không phải chờ đợi)
            const finalAnswer = (botElement.dataset.raw || "").trim();
            if (finalAnswer && !finalAnswer.startsWith("**Something went wrong:**")) {
                fetchParallelFollowups(submittedMessage, finalAnswer, botElement, selectedModel);
            }
        }
    }

    // === KHỐI 19: Upload tài liệu và bắt đầu chat mới ===
    async function uploadFile() {
        const fileInput = document.getElementById("fileInput");

        if (!fileInput.files.length) {
            return;
        }

        pendingFile = fileInput.files[0];
        showPendingAttachment(pendingFile);
        fileInput.value = "";
    }

    async function newChat() {
        clearPendingAttachment();
        threadId = crypto.randomUUID();
        isTemporaryChat = false;
        const tempBtn = document.getElementById("tempChatToggle");
        if (tempBtn) {
            tempBtn.classList.remove("active");
            tempBtn.style.display = "inline-flex";
        }

        if (currentUser) {
            // Đã đăng nhập: lưu thread_id để khôi phục khi reload
            localStorage.setItem("thread_id", threadId);
        } else {
            activeProjectId = "";
            activeProject = null;
            localStorage.removeItem("active_project_id");
            // Guest: không lưu, mỗi phiên là tạm thời — thread cũ sẽ tự expire sau 24h
            localStorage.removeItem("thread_id");
        }

        if (isDictating) {
            stopDictation();
        }

        renderWelcome();
        updateProjectWorkspace();
        await loadConversations();
        messageInput.focus();
    }


    // ---- Tit logo: eyes follow the cursor, blinks, click to interact ----
    const brandLogoEl = document.getElementById("brandLogo");
    if (brandLogoEl) brandLogoEl.innerHTML = TIT_LOGO;

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const TIT_LINES = [
        "Quạ quạ! Xin chào bạn",
        "Mình đang nghe đây",
        "Hỏi mình bất cứ điều gì nhé",
        "Đừng chọc mình, quạ giận đó",
        "Thử bấm vào một thẻ gợi ý xem",
        "Hôm nay bạn muốn làm gì?"
    ];
    let titLineIndex = Math.floor(Math.random() * TIT_LINES.length);
    let titClicks = [];
    let titBubbleTimer = null;

    let eyeFrame = null;
    document.addEventListener("pointermove", (e) => {
        if (eyeFrame) return;
        eyeFrame = requestAnimationFrame(() => {
            eyeFrame = null;
            document.querySelectorAll(".tit-logo").forEach(logo => {
                const pupil = logo.querySelector(".tit-pupil");
                if (!pupil) return;
                const r = logo.getBoundingClientRect();
                const dx = e.clientX - (r.left + r.width * 0.37);
                const dy = e.clientY - (r.top + r.height * 0.25);
                const dist = Math.hypot(dx, dy) || 1;
                const k = Math.min(1, dist / 160) * 1.2;
                pupil.setAttribute("transform", `translate(${(dx / dist * k).toFixed(2)} ${(dy / dist * k).toFixed(2)})`);
            });
        });
    }, { passive: true });

    // === KHỐI 20: Hoạt ảnh và tương tác mascot ===
    function titBlink() {
        document.querySelectorAll(".tit-logo").forEach(l => l.classList.add("blink"));
        setTimeout(() => document.querySelectorAll(".tit-logo").forEach(l => l.classList.remove("blink")), 140);
    }
    (function scheduleBlink() {
        setTimeout(() => {
            titBlink();
            if (Math.random() < 0.25) setTimeout(titBlink, 260);
            scheduleBlink();
        }, 2600 + Math.random() * 3400);
    })();

    function playOnce(el, cls) {
        el.classList.remove(cls);
        void el.getBoundingClientRect();
        el.classList.add(cls);
        const done = () => { el.classList.remove(cls); el.removeEventListener("animationend", done); };
        el.addEventListener("animationend", done);
    }

    function titSay(text, ms = 2800) {
        const bubble = document.querySelector(".tit-bubble");
        if (!bubble) return;
        bubble.textContent = text;
        bubble.classList.add("show");
        clearTimeout(titBubbleTimer);
        titBubbleTimer = setTimeout(() => bubble.classList.remove("show"), ms);
    }

    function titBurst(container, count) {
        if (prefersReducedMotion || !container.animate) return;
        const colors = ["#ff2b3d", "#ff7a85", "#d62839", "#ff9a4d", "#ff4d5a", "#ffffff"];

        const wave = document.createElement("span");
        wave.className = "tit-wave";
        container.appendChild(wave);
        wave.animate(
            [{ transform: "scale(0.7)", opacity: 0.9 }, { transform: "scale(2.6)", opacity: 0 }],
            { duration: 700, easing: "cubic-bezier(0.16, 1, 0.3, 1)" }
        ).onfinish = () => wave.remove();

        for (let i = 0; i < count; i++) {
            const p = document.createElement("span");
            p.className = "tit-spark" + (i % 3 === 0 ? " sq" : "");
            p.style.background = colors[i % colors.length];
            p.style.color = colors[i % colors.length];
            container.appendChild(p);
            const ang = (i / count) * Math.PI * 2 + Math.random() * 0.5;
            const dist = 78 + Math.random() * 52;
            const x = Math.cos(ang) * dist, y = Math.sin(ang) * dist - 8;
            const rot = (Math.random() - 0.5) * 360;
            p.animate([
                { transform: "translate(-50%, -50%) scale(0.5) rotate(0)", opacity: 1, easing: "cubic-bezier(0.16, 1, 0.3, 1)" },
                { transform: `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(1) rotate(${rot / 2}deg)`, opacity: 1, offset: 0.6, easing: "ease-in" },
                { transform: `translate(calc(-50% + ${x * 1.1}px), calc(-50% + ${y * 1.1 + 22}px)) scale(0) rotate(${rot}deg)`, opacity: 0 }
            ], { duration: 1100 + Math.random() * 400, easing: "linear" }).onfinish = () => p.remove();
        }
    }

    function titInteract(core) {
        const logo = core.querySelector(".tit-logo");
        const orbit = core.closest(".orbit");
        if (!logo || !orbit) return;

        const now = Date.now();
        titClicks = titClicks.filter(t => now - t < 2000);
        titClicks.push(now);
        const frantic = titClicks.length >= 5;

        // the crow's eye flashes red for a moment
        logo.classList.add("redeye");
        clearTimeout(logo._eyeTimer);
        logo._eyeTimer = setTimeout(() => logo.classList.remove("redeye"), frantic ? 2200 : 900);

        if (!prefersReducedMotion) {
            playOnce(logo, frantic ? "flip" : "hop");
            logo.classList.remove("flap");
            void logo.getBoundingClientRect();
            logo.classList.add("flap");
            setTimeout(() => logo.classList.remove("flap"), 950);
        }

        if (frantic) {
            titClicks = [];
            titBurst(orbit, 22);
            titSay("Chóng mặt rồi, dừng lại đi!", 3200);
        } else {
            titBurst(orbit, 12);
            titLineIndex = (titLineIndex + 1) % TIT_LINES.length;
            titSay(TIT_LINES[titLineIndex]);
        }
    }

    chatContainer.addEventListener("click", (e) => {
        const core = e.target.closest(".orbit-core");
        if (core) titInteract(core);
    });
    chatContainer.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        const core = e.target.closest(".orbit-core");
        if (core) {
            e.preventDefault();
            titInteract(core);
        }
    });

    if (brandLogoEl) {
        const brandHop = () => {
            const logo = brandLogoEl.querySelector(".tit-logo");
            if (logo && !prefersReducedMotion) playOnce(logo, "hop");
        };
        brandLogoEl.addEventListener("click", brandHop);
        brandLogoEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); brandHop(); }
        });
    }

    // Greeting on first load, once the hero has finished animating in
    setTimeout(() => {
        const w = document.getElementById("welcome");
        if (w && w.style.display !== "none") titSay("Quạ quạ! Bấm vào mình thử nhé", 3400);
    }, 2300);

    // ==========================================
    // User Authentication & Session Management
    // ==========================================
    let currentUser = null;

    // === KHỐI 21: Session và hiển thị người dùng ===
    async function checkAuthStatus() {
        try {
            const res = await fetch("/api/auth/me");
            if (res.ok) {
                const data = await res.json();
                currentUser = data.user || (data.authenticated ? data.user : null);
            } else {
                currentUser = null;
            }
        } catch (err) {
            console.error("Failed to check auth status:", err);
            currentUser = null;
        }
        renderUserUI();
    }

    function renderUserUI() {
        const sidebarSec = document.getElementById("sidebarUserSection");
        const topbarArea = document.getElementById("topbarUserArea");
        if (!sidebarSec || !topbarArea) return;

        if (currentUser) {
            const displayName = currentUser.full_name || currentUser.username;
            const initial = (currentUser.username || "U").charAt(0).toUpperCase();
            const avatarHtml = currentUser.avatar_url
                ? `<img src="${escapeHtml(currentUser.avatar_url)}" class="sidebar-user-avatar-img" alt="Avatar" onerror="this.outerHTML='<div class=\\'sidebar-user-avatar\\'>${escapeHtml(initial)}</div>'">`
                : `<div class="sidebar-user-avatar">${escapeHtml(initial)}</div>`;

            sidebarSec.innerHTML = `
                <div class="user-account-menu" id="userAccountMenu" role="menu" aria-label="Menu tài khoản">
                    <button type="button" class="user-menu-item" role="menuitem" onclick="closeUserAccountMenu(); openPersonalizationModal('style')">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line>
                            <line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line>
                            <line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line>
                            <line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line>
                        </svg>
                        <span>Personalization</span>
                    </button>
                    <button type="button" class="user-menu-item" role="menuitem" onclick="closeUserAccountMenu(); openProfileModal()">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                            <circle cx="12" cy="7" r="4"></circle>
                        </svg>
                        <span>Profile</span>
                    </button>
                    <button type="button" class="user-menu-item" role="menuitem" onclick="closeUserAccountMenu(); handleClaimOfferClick()">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="20 12 20 22 4 22 4 12"></polyline>
                            <rect x="2" y="7" width="20" height="5"></rect>
                            <line x1="12" y1="22" x2="12" y2="7"></line>
                            <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"></path>
                            <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"></path>
                        </svg>
                        <span>Claim offer</span>
                        <span class="user-menu-item-badge">Soon</span>
                    </button>
                    <div class="user-menu-divider"></div>
                    <button type="button" class="user-menu-item danger" role="menuitem" onclick="closeUserAccountMenu(); handleSignOut()">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                            <polyline points="16 17 21 12 16 7"></polyline>
                            <line x1="21" y1="12" x2="9" y2="12"></line>
                        </svg>
                        <span>Log out</span>
                    </button>
                </div>
                <div class="user-profile-card clickable" onclick="toggleUserAccountMenu(event)" title="Tùy chọn tài khoản" role="button" tabindex="0" aria-haspopup="menu">
                    ${avatarHtml}
                    <div class="user-info">
                        <div class="user-name" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</div>
                        <div class="user-handle" title="@${escapeHtml(currentUser.username)}">@${escapeHtml(currentUser.username)}</div>
                    </div>
                    <div class="edit-badge" title="Mở menu tài khoản">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="18 15 12 9 6 15"></polyline>
                        </svg>
                    </div>
                </div>
            `;

            const topbarAvatarHtml = currentUser.avatar_url
                ? `<img src="${escapeHtml(currentUser.avatar_url)}" class="pill-avatar-img" alt="Avatar" onerror="this.outerHTML='<div class=\\'pill-avatar\\'>${escapeHtml(initial)}</div>'">`
                : `<div class="pill-avatar">${escapeHtml(initial)}</div>`;

            // Liên kết chỉ cải thiện điều hướng; quyền truy cập vẫn được kiểm tra tại backend.
            topbarArea.innerHTML = `
                ${currentUser.role === "admin" ? `<a class="topbar-user-pill" href="/admin" title="Open admin panel">Admin</a>` : ""}
                <div class="topbar-user-pill clickable" onclick="openPersonalizationModal('style')" title="Personalization & Cài đặt: ${escapeHtml(currentUser.username)}" role="button" tabindex="0">
                    ${topbarAvatarHtml}
                    <span>${escapeHtml(displayName)}</span>
                </div>
            `;
        } else {
            sidebarSec.innerHTML = `
                <button class="auth-open-btn" onclick="openAuthModal('signin')">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path>
                        <polyline points="10 17 15 12 10 7"></polyline>
                        <line x1="15" y1="12" x2="3" y2="12"></line>
                    </svg>
                    <span>Đăng nhập / Đăng ký</span>
                </button>
            `;

            topbarArea.innerHTML = `
                <button class="topbar-login-btn" onclick="openAuthModal('signin')">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path>
                        <polyline points="10 17 15 12 10 7"></polyline>
                        <line x1="15" y1="12" x2="3" y2="12"></line>
                    </svg>
                    <span>Đăng nhập</span>
                </button>
            `;
        }
    }

    // ---------- User Account Menu & Personalization Settings ----------
    let persSelectedStyle = "default";
    let persMemoriesCache = [];
    let persHasSummarized = false;
    let persActiveTab = "style";

    /** Bật/tắt menu tài khoản ở góc dưới sidebar. */
    function toggleUserAccountMenu(event) {
        if (event) event.stopPropagation();
        const menu = document.getElementById("userAccountMenu");
        if (!menu) return;
        menu.classList.toggle("open");
    }

    /** Đóng menu tài khoản nếu đang mở. */
    function closeUserAccountMenu() {
        const menu = document.getElementById("userAccountMenu");
        if (menu) menu.classList.remove("open");
    }

    /** Thông báo tính năng Claim offer đang phát triển. */
    function handleClaimOfferClick() {
        alert("Tính năng Claim offer sẽ sớm ra mắt trong phiên bản tiếp theo.");
    }

    document.addEventListener("click", (event) => {
        const menu = document.getElementById("userAccountMenu");
        const sec = document.getElementById("sidebarUserSection");
        if (menu && menu.classList.contains("open") && sec && !sec.contains(event.target)) {
            menu.classList.remove("open");
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeUserAccountMenu();
            const persModal = document.getElementById("personalizationModal");
            if (persModal && persModal.style.display === "flex") {
                closePersonalizationModal();
            }
        }
    });

    // === KHỐI 22: Cá nhân hóa: phong cách, hướng dẫn và bộ nhớ ===
    function _setPersBanner(type, message) {
        const errEl = document.getElementById("persErrorBanner");
        const okEl = document.getElementById("persSuccessBanner");
        if (errEl) {
            errEl.style.display = type === "error" && message ? "flex" : "none";
            errEl.textContent = type === "error" && message ? message : "";
        }
        if (okEl) {
            okEl.style.display = type === "success" && message ? "flex" : "none";
            okEl.textContent = type === "success" && message ? message : "";
        }
    }

    /** Chọn một trong 5 Base style & tone cards. */
    function selectBaseStyle(style) {
        const normalized = (style || "default").toLowerCase();
        persSelectedStyle = normalized;
        document.querySelectorAll("#persToneGrid .pers-tone-card").forEach((card) => {
            card.classList.toggle("active", card.dataset.style === normalized);
        });
    }

    /** Cập nhật bộ đếm ký tự của Custom Instructions. */
    function updatePersCharCount() {
        const textarea = document.getElementById("persCustomInstructions");
        const countEl = document.getElementById("persCharCount");
        if (!textarea || !countEl) return;
        countEl.textContent = `${textarea.value.length} / 1500`;
    }

    /** Chèn nhanh một mẫu quy tắc vào ô Custom Instructions. */
    function appendCustomInstructionPreset(text) {
        const textarea = document.getElementById("persCustomInstructions");
        if (!textarea || !text) return;
        const current = textarea.value.trim();
        if (current.includes(text)) return;
        const combined = current ? `${current}\n${text}` : text;
        textarea.value = combined.slice(0, 1500);
        updatePersCharCount();
        textarea.focus();
    }

    /** Mở modal Personalization và đồng bộ dữ liệu từ server. */
    function openPersonalizationModal(tab = "style") {
        if (!currentUser) {
            openAuthModal("signin");
            return;
        }
        closeUserAccountMenu();
        _setPersBanner(null, "");
        persHasSummarized = false;

        const existing = currentUser.personalization || {};
        selectBaseStyle(existing.base_style || "default");
        const textarea = document.getElementById("persCustomInstructions");
        if (textarea) {
            textarea.value = existing.custom_instructions || "";
            updatePersCharCount();
        }

        const modal = document.getElementById("personalizationModal");
        if (modal) modal.style.display = "flex";
        switchPersonalizationTab(tab);
        loadPersonalizationData();
    }

    /** Đóng modal Personalization. */
    function closePersonalizationModal() {
        const modal = document.getElementById("personalizationModal");
        if (modal) modal.style.display = "none";
    }

    /** Chuyển giữa tab Phong cách & Hướng dẫn và tab Bộ nhớ (Memory). */
    function switchPersonalizationTab(tab) {
        persActiveTab = tab === "memory" ? "memory" : "style";
        const navStyle = document.getElementById("persNavStyle");
        const navMem = document.getElementById("persNavMemory");
        const tabStyle = document.getElementById("persTabStyle");
        const tabMem = document.getElementById("persTabMemory");

        if (navStyle) navStyle.classList.toggle("active", persActiveTab === "style");
        if (navMem) navMem.classList.toggle("active", persActiveTab === "memory");
        if (tabStyle) tabStyle.style.display = persActiveTab === "style" ? "block" : "none";
        if (tabMem) tabMem.style.display = persActiveTab === "memory" ? "block" : "none";

        if (persActiveTab === "memory" && !persHasSummarized) {
            refreshMemorySummary();
        }
    }

    /** Tải cài đặt personalization và danh sách memory cá nhân từ API. */
    async function loadPersonalizationData() {
        try {
            const res = await fetch("/api/personalization");
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể tải cài đặt Personalization.");
            }
            const pers = data.personalization || {};
            selectBaseStyle(pers.base_style || "default");
            const textarea = document.getElementById("persCustomInstructions");
            if (textarea) {
                textarea.value = pers.custom_instructions || "";
                updatePersCharCount();
            }
            if (currentUser) currentUser.personalization = pers;
            persMemoriesCache = Array.isArray(data.memories) ? data.memories : [];
            renderPersFactsList(persMemoriesCache);
            const countLabel = document.getElementById("persMemoryCountLabel");
            if (countLabel && !persHasSummarized) {
                countLabel.textContent = `${persMemoriesCache.length} mục bộ nhớ cá nhân đang hoạt động`;
            }
        } catch (err) {
            _setPersBanner("error", err.message || "Lỗi tải dữ liệu Personalization.");
        }
    }

    /** Lưu Base style & tone và Custom Instructions xuống MongoDB. */
    async function savePersonalizationSettings() {
        const btn = document.getElementById("persSaveBtn");
        const textarea = document.getElementById("persCustomInstructions");
        const customInstructions = textarea ? textarea.value : "";

        _setPersBanner(null, "");
        if (btn) btn.disabled = true;
        try {
            const res = await fetch("/api/personalization", {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    base_style: persSelectedStyle,
                    custom_instructions: customInstructions,
                }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Lưu cài đặt thất bại.");
            }
            if (data.user) {
                currentUser = data.user;
            } else if (currentUser) {
                currentUser.personalization = data.personalization;
            }
            _setPersBanner("success", data.message || "Đã lưu cài đặt Personalization!");
        } catch (err) {
            _setPersBanner("error", err.message || "Không thể lưu cài đặt.");
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /** Hiển thị danh sách từng bản ghi long_term_memory gốc kèm nút xóa. */
    function renderPersFactsList(memories) {
        const listEl = document.getElementById("persFactsList");
        if (!listEl) return;
        const items = Array.isArray(memories) ? memories : [];
        if (items.length === 0) {
            listEl.innerHTML = `<div class="pers-fact-item"><span style="color: var(--text-muted);">Chưa có ghi nhớ cá nhân nào được lưu.</span></div>`;
            return;
        }
        listEl.innerHTML = items.map((item) => {
            const memId = escapeHtml(String(item.memory_id || ""));
            const memText = escapeHtml(String(item.memory || ""));
            return `
                <div class="pers-fact-item">
                    <span>${memText}</span>
                    <button type="button" class="pers-btn-secondary" style="padding: 4px 9px;" onclick="deletePersonalMemory('${memId}')">Xóa</button>
                </div>
            `;
        }).join("");
    }

    function _renderPersMemoryResult(data) {
        const bodyEl = document.getElementById("persMemorySummaryBody");
        const countEl = document.getElementById("persMemoryCountLabel");
        const memories = Array.isArray(data.memories) ? data.memories : [];
        persMemoriesCache = memories;

        if (countEl) {
            countEl.textContent = `${memories.length} mục bộ nhớ cá nhân đang hoạt động`;
        }
        if (bodyEl) {
            const summaryText = String(data.summary || "Chưa có thông tin bộ nhớ cá nhân nào được lưu.");
            if (typeof renderMarkdown === "function") {
                renderMarkdown(bodyEl, summaryText);
            } else {
                bodyEl.textContent = summaryText;
            }
        }
        renderPersFactsList(memories);
    }

    /** Gọi LLM tóm tắt toàn bộ long_term_memory lọc theo user_id. */
    async function refreshMemorySummary() {
        const btn = document.getElementById("persSummarizeBtn");
        const bodyEl = document.getElementById("persMemorySummaryBody");
        _setPersBanner(null, "");
        if (btn) btn.disabled = true;
        if (bodyEl) bodyEl.textContent = "Đang dùng AI tổng hợp bộ nhớ của bạn...";
        try {
            const res = await fetch("/api/personalization/memory-summary", { method: "POST" });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể tóm tắt bộ nhớ.");
            }
            persHasSummarized = true;
            _renderPersMemoryResult(data);
        } catch (err) {
            if (bodyEl) bodyEl.textContent = "Không thể tổng hợp bộ nhớ lúc này.";
            _setPersBanner("error", err.message || "Lỗi tóm tắt bộ nhớ.");
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /** Gửi lệnh tự nhiên để LLM thêm/sửa/xóa long_term_memory cá nhân. */
    async function runAiMemoryUpdate() {
        const input = document.getElementById("persMemoryAiInput");
        const btn = document.getElementById("persAiUpdateBtn");
        const instruction = input ? input.value.trim() : "";
        if (!instruction) {
            _setPersBanner("error", "Vui lòng nhập yêu cầu cập nhật bộ nhớ.");
            return;
        }
        _setPersBanner(null, "");
        if (btn) btn.disabled = true;
        try {
            const res = await fetch("/api/personalization/memory-ai-update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ instruction }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể cập nhật bộ nhớ bằng AI.");
            }
            persHasSummarized = true;
            _renderPersMemoryResult(data);
            if (input) input.value = "";
            const actions = Array.isArray(data.applied_actions) ? data.applied_actions : [];
            const msg = data.note || (actions.length ? `Đã cập nhật ${actions.length} mục bộ nhớ.` : "Đã kiểm tra và cập nhật bộ nhớ.");
            _setPersBanner("success", msg);
        } catch (err) {
            _setPersBanner("error", err.message || "Lỗi cập nhật bộ nhớ bằng AI.");
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /** Xóa một mục bộ nhớ cá nhân cụ thể theo memory_id. */
    async function deletePersonalMemory(memoryId) {
        if (!memoryId) return;
        _setPersBanner(null, "");
        try {
            const res = await fetch(`/api/personalization/memories/${encodeURIComponent(memoryId)}`, {
                method: "DELETE",
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể xóa mục bộ nhớ.");
            }
            persMemoriesCache = persMemoriesCache.filter((m) => String(m.memory_id) !== String(memoryId));
            renderPersFactsList(persMemoriesCache);
            await refreshMemorySummary();
            _setPersBanner("success", "Đã xóa mục bộ nhớ.");
        } catch (err) {
            _setPersBanner("error", err.message || "Lỗi xóa mục bộ nhớ.");
        }
    }

    /** Xóa toàn bộ bộ nhớ cá nhân sau khi người dùng xác nhận. */
    async function clearAllPersonalMemories() {
        if (!confirm("Bạn có chắc muốn xóa toàn bộ bộ nhớ cá nhân không?")) return;
        _setPersBanner(null, "");
        try {
            const res = await fetch("/api/personalization/memories", { method: "DELETE" });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể xóa toàn bộ bộ nhớ.");
            }
            persHasSummarized = true;
            _renderPersMemoryResult({
                summary: "Chưa có thông tin bộ nhớ cá nhân nào được lưu.",
                memories: [],
                count: 0,
            });
            _setPersBanner("success", "Đã xóa toàn bộ bộ nhớ cá nhân.");
        } catch (err) {
            _setPersBanner("error", err.message || "Lỗi xóa bộ nhớ cá nhân.");
        }
    }

    let authChallengeActive = false;
    let otpCountdownTimer = null;

    // ---------- ChatGPT-style Search Chats Modal ----------
    let searchDebounceTimer = null;
    let searchAbortController = null;
    let searchFlatItems = [];
    let searchSelectedIndex = -1;

    // === KHỐI 23: Tìm kiếm lịch sử chat ===
    function _highlightSearchMatch(rawText, query) {
        const text = String(rawText || "");
        const cleanQuery = String(query || "").trim();
        if (!cleanQuery) return escapeHtml(text);
        const lowerText = text.toLowerCase();
        const lowerQuery = cleanQuery.toLowerCase();
        let pos = 0;
        let out = "";
        while (pos < text.length) {
            const idx = lowerText.indexOf(lowerQuery, pos);
            if (idx === -1) {
                out += escapeHtml(text.slice(pos));
                break;
            }
            out += escapeHtml(text.slice(pos, idx));
            out += `<mark class="search-highlight">${escapeHtml(text.slice(idx, idx + cleanQuery.length))}</mark>`;
            pos = idx + cleanQuery.length;
        }
        return out;
    }

    function _getSearchTimeGroup(isoString) {
        if (!isoString) return "Trước đó";
        const ts = Date.parse(isoString);
        if (Number.isNaN(ts)) return "Trước đó";
        const now = new Date();
        const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        if (ts >= startOfToday) return "Hôm nay";
        if (ts >= startOfToday - 7 * 86400000) return "7 ngày qua";
        return "Trước đó";
    }

    /** Mở modal tìm kiếm đoạn chat (giống ChatGPT Ctrl+K). */
    function openSearchModal() {
        const modal = document.getElementById("searchChatsModal");
        const input = document.getElementById("searchChatsInput");
        const clearBtn = document.getElementById("searchChatsClearBtn");
        if (!modal || !input) return;
        modal.style.display = "flex";
        input.value = "";
        if (clearBtn) clearBtn.style.display = "none";
        searchSelectedIndex = -1;
        setTimeout(() => input.focus(), 20);
        runConversationSearch("");
    }

    /** Đóng modal tìm kiếm đoạn chat và hủy request cũ nếu đang chạy. */
    function closeSearchModal() {
        const modal = document.getElementById("searchChatsModal");
        if (modal) modal.style.display = "none";
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        if (searchAbortController) searchAbortController.abort();
    }

    function clearSearchChatsInput() {
        const input = document.getElementById("searchChatsInput");
        const clearBtn = document.getElementById("searchChatsClearBtn");
        if (input) {
            input.value = "";
            input.focus();
        }
        if (clearBtn) clearBtn.style.display = "none";
        runConversationSearch("");
    }

    function onSearchChatsInput() {
        const input = document.getElementById("searchChatsInput");
        const clearBtn = document.getElementById("searchChatsClearBtn");
        const query = input ? input.value : "";
        if (clearBtn) clearBtn.style.display = query.trim() ? "inline-flex" : "none";
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => runConversationSearch(query), 200);
    }

    /** Gọi API /api/conversations/search và render kết quả theo nhóm hoặc từ khóa. */
    async function runConversationSearch(rawQuery) {
        const container = document.getElementById("searchChatsResults");
        if (!container) return;

        const query = String(rawQuery || "").trim();
        if (!currentUser) {
            searchFlatItems = [{ type: "new_chat" }];
            searchSelectedIndex = 0;
            container.innerHTML = `
                <button type="button" class="search-result-item selected" data-search-idx="0" onclick="triggerSearchSelection(0)">
                    <span class="search-result-icon">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                    </span>
                    <div class="search-result-main">
                        <div class="search-result-title">Đoạn chat mới</div>
                    </div>
                </button>
                <div class="search-empty-state">Đăng nhập để tìm kiếm toàn bộ lịch sử trò chuyện và Project của bạn.</div>
            `;
            return;
        }

        if (searchAbortController) searchAbortController.abort();
        searchAbortController = new AbortController();

        try {
            const url = `/api/conversations/search?q=${encodeURIComponent(query)}&limit=25`;
            const res = await fetch(url, { signal: searchAbortController.signal });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Không thể tìm kiếm cuộc trò chuyện.");
            }
            renderSearchResults(data.results || [], query);
        } catch (err) {
            if (err && err.name === "AbortError") return;
            container.innerHTML = `<div class="search-empty-state">Không thể tải kết quả tìm kiếm lúc này.</div>`;
        }
    }

    function renderSearchResults(results, query) {
        const container = document.getElementById("searchChatsResults");
        if (!container) return;

        searchFlatItems = [];
        let html = "";

        if (!query) {
            const idx = searchFlatItems.length;
            searchFlatItems.push({ type: "new_chat" });
            html += `
                <button type="button" class="search-result-item" data-search-idx="${idx}" onclick="triggerSearchSelection(${idx})">
                    <span class="search-result-icon">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>
                    </span>
                    <div class="search-result-main">
                        <div class="search-result-title">Đoạn chat mới</div>
                    </div>
                </button>
            `;
        }

        if (!results || results.length === 0) {
            if (query) {
                html = `<div class="search-empty-state">Không tìm thấy đoạn chat nào khớp với "<strong>${escapeHtml(query)}</strong>".</div>`;
            } else {
                html += `<div class="search-empty-state">Chưa có lịch sử đoạn chat nào.</div>`;
            }
            container.innerHTML = html;
            searchSelectedIndex = searchFlatItems.length > 0 ? 0 : -1;
            _syncSearchActiveSelection();
            return;
        }

        let currentGroup = "";
        results.forEach((item) => {
            const groupLabel = query ? "Kết quả phù hợp" : _getSearchTimeGroup(item.updated_at);
            if (groupLabel !== currentGroup) {
                currentGroup = groupLabel;
                html += `<div class="search-group-label">${escapeHtml(groupLabel)}</div>`;
            }
            const idx = searchFlatItems.length;
            searchFlatItems.push({ type: "chat", item });

            const iconSvg = item.project_id
                ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
                : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;

            const titleHtml = _highlightSearchMatch(item.title || "Đoạn chat mới", query);
            const projBadge = item.project_name
                ? `<span class="search-project-pill">${escapeHtml(item.project_name)}</span>`
                : "";
            const snippetHtml = item.snippet
                ? `<div class="search-result-snippet">${_highlightSearchMatch(item.snippet, query)}</div>`
                : "";

            html += `
                <button type="button" class="search-result-item" data-search-idx="${idx}" onclick="triggerSearchSelection(${idx})">
                    <span class="search-result-icon">${iconSvg}</span>
                    <div class="search-result-main">
                        <div class="search-result-top">
                            <span class="search-result-title">${titleHtml}</span>
                            ${projBadge}
                        </div>
                        ${snippetHtml}
                    </div>
                </button>
            `;
        });

        container.innerHTML = html;
        searchSelectedIndex = searchFlatItems.length > 0 ? (query ? 0 : (searchFlatItems.length > 1 ? 1 : 0)) : -1;
        _syncSearchActiveSelection();
    }

    function _syncSearchActiveSelection() {
        const container = document.getElementById("searchChatsResults");
        if (!container) return;
        container.querySelectorAll(".search-result-item").forEach((el) => {
            const idx = Number(el.dataset.searchIdx);
            const active = idx === searchSelectedIndex;
            el.classList.toggle("selected", active);
            if (active) el.scrollIntoView({ block: "nearest" });
        });
    }

    function handleSearchModalKeydown(event) {
        if (event.key === "Escape") {
            event.preventDefault();
            closeSearchModal();
            return;
        }
        if (searchFlatItems.length === 0) return;
        if (event.key === "ArrowDown") {
            event.preventDefault();
            searchSelectedIndex = (searchSelectedIndex + 1) % searchFlatItems.length;
            _syncSearchActiveSelection();
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            searchSelectedIndex = (searchSelectedIndex - 1 + searchFlatItems.length) % searchFlatItems.length;
            _syncSearchActiveSelection();
        } else if (event.key === "Enter") {
            event.preventDefault();
            if (searchSelectedIndex >= 0 && searchSelectedIndex < searchFlatItems.length) {
                triggerSearchSelection(searchSelectedIndex);
            }
        }
    }

    function triggerSearchSelection(idx) {
        const entry = searchFlatItems[idx];
        if (!entry) return;
        closeSearchModal();
        if (entry.type === "new_chat") {
            newChat();
            return;
        }
        if (entry.item && entry.item.thread_id) {
            loadConversation(entry.item.thread_id, entry.item.project_id || "");
        }
    }

    // Phím tắt Ctrl+K / Cmd+K để mở nhanh hộp tìm kiếm đoạn chat giống ChatGPT
    window.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            const modal = document.getElementById("searchChatsModal");
            if (modal && modal.style.display === "flex") {
                closeSearchModal();
            } else {
                openSearchModal();
            }
        }
    });

    // === KHỐI 24: Đăng nhập, đăng ký và xác minh OTP ===
    function openAuthModal(tab = "signin") {
        const modal = document.getElementById("authModal");
        if (!modal) return;
        modal.style.display = "flex";
        hideAuthError();
        if (authChallengeActive) return;
        switchAuthTab(tab);
        restoreAuthChallenge();
    }

    function closeAuthModal() {
        const modal = document.getElementById("authModal");
        if (!modal) return;
        modal.style.display = "none";
        hideAuthError();
        if (authChallengeActive) return;
        document.getElementById("signInForm")?.reset();
        document.getElementById("signUpForm")?.reset();
    }

    function showAuthError(msg) {
        const banner = document.getElementById("authErrorBanner");
        if (!banner) return;
        banner.className = "auth-error-banner";
        banner.textContent = msg;
        banner.style.display = "flex";
    }

    function showAuthNotice(msg) {
        const banner = document.getElementById("authErrorBanner");
        if (!banner) return;
        banner.className = "auth-success-banner";
        banner.textContent = msg;
        banner.style.display = "flex";
    }

    function hideAuthError() {
        const banner = document.getElementById("authErrorBanner");
        if (!banner) return;
        banner.className = "auth-error-banner";
        banner.textContent = "";
        banner.style.display = "none";
    }

    function switchAuthTab(tab) {
        if (authChallengeActive) return;
        hideAuthError();
        document.getElementById("authTabs").style.display = "flex";
        document.getElementById("otpForm").style.display = "none";
        document.getElementById("signInForm").style.display = tab === "signin" ? "block" : "none";
        document.getElementById("signUpForm").style.display = tab === "signup" ? "block" : "none";
        document.getElementById("tabSignInBtn").classList.toggle("active", tab === "signin");
        document.getElementById("tabSignUpBtn").classList.toggle("active", tab === "signup");
        document.getElementById("authModalTitle").textContent = tab === "signin" ? "Đăng nhập" : "Đăng ký tài khoản";
        document.getElementById("authModalDesc").textContent = tab === "signin"
            ? "Đăng nhập để đồng bộ và lưu trữ lịch sử trò chuyện"
            : "Tạo tài khoản mới để sở hữu không gian chat và tài liệu riêng";
        setTimeout(() => document.getElementById(tab === "signin" ? "signInUsername" : "signUpUsername")?.focus(), 50);
    }

    async function showOtpScreen(email) {
        authChallengeActive = true;
        document.getElementById("authTabs").style.display = "none";
        document.getElementById("signInForm").style.display = "none";
        document.getElementById("signUpForm").style.display = "none";
        document.getElementById("otpForm").style.display = "block";
        document.getElementById("authModalTitle").textContent = "Xác minh email";
        document.getElementById("authModalDesc").textContent = `Nhập mã 6 chữ số đã gửi đến ${email || "email của bạn"}.`;
        document.querySelectorAll(".otp-digit").forEach((input) => { input.value = ""; });
        document.getElementById("otpDigit0")?.focus();
        const response = await fetch("/api/auth/challenge");
        const data = await response.json();
        if (data.active) updateOtpChallenge(data.challenge);
    }

    function updateOtpChallenge(challenge) {
        const resend = document.getElementById("resendCodeBtn");
        const label = document.getElementById("resendCountdown");
        if (otpCountdownTimer) clearInterval(otpCountdownTimer);
        const update = () => {
            const wait = Math.max(0, Math.ceil((new Date(challenge.resend_available_at).getTime() - Date.now()) / 1000));
            resend.disabled = wait > 0 || !challenge.can_resend;
            label.textContent = !challenge.can_resend ? "Đã hết lượt gửi lại mã." : wait ? `Gửi lại sau ${wait}s` : "";
            if (!challenge.expired && Date.now() >= new Date(challenge.expires_at).getTime()) {
                challenge.expired = true;
                showAuthError("Mã đã hết hạn. Bạn có thể gửi mã mới để tiếp tục.");
            }
        };
        update();
        otpCountdownTimer = setInterval(update, 1000);
        if (challenge.expired) showAuthError("Mã đã hết hạn. Bạn có thể gửi mã mới để tiếp tục.");
    }

    async function restoreAuthChallenge() {
        try {
            const response = await fetch("/api/auth/challenge");
            const data = await response.json();
            if (data.active) {
                document.getElementById("authModal").style.display = "flex";
                await showOtpScreen(data.challenge.email);
                updateOtpChallenge(data.challenge);
            }
        } catch (error) { console.error("Could not restore verification challenge:", error); }
    }

    async function completeAuthResponse(response) {
        const data = await response.json();
        if (data.requires_verification) {
            await showOtpScreen(data.email);
            if (!response.ok) showAuthError(data.error || "Không thể gửi mã xác minh.");
            return;
        }
        if (!response.ok || !data.success) {
            showAuthError(data.error || data.detail || "Yêu cầu thất bại.");
            return;
        }
        currentUser = data.user;
        renderUserUI();
        authChallengeActive = false;
        closeAuthModal();
        localStorage.removeItem("thread_id");
        await loadProjects();
        await newChat();
        await showProjectInvitationLink();
    }

    async function handleSignInSubmit(event) {
        event.preventDefault();
        hideAuthError();
        const identifier = (document.getElementById("signInUsername").value || "").trim();
        const password = document.getElementById("signInPassword").value;
        const btn = document.getElementById("signInSubmitBtn");
        if (!identifier || !password) return showAuthError("Vui lòng nhập tên đăng nhập và mật khẩu.");
        btn.disabled = true;
        try {
            const response = await fetch("/api/auth/signin", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ identifier, password }),
            });
            await completeAuthResponse(response);
        } catch (error) {
            console.error("Sign in error:", error);
            showAuthError("Có lỗi kết nối tới máy chủ.");
        } finally { btn.disabled = false; }
    }

    async function handleSignUpSubmit(event) {
        event.preventDefault();
        hideAuthError();
        const btn = document.getElementById("signUpSubmitBtn");
        const body = {
            username: document.getElementById("signUpUsername").value.trim(),
            email: document.getElementById("signUpEmail").value.trim(),
            full_name: document.getElementById("signUpFullName").value.trim(),
            password: document.getElementById("signUpPassword").value,
        };
        if (!body.username || !body.email || !body.password) return showAuthError("Vui lòng điền các trường bắt buộc.");
        btn.disabled = true;
        try {
            await completeAuthResponse(await fetch("/api/auth/signup", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            }));
        } catch (error) {
            console.error("Sign up error:", error);
            showAuthError("Có lỗi kết nối tới máy chủ.");
        } finally { btn.disabled = false; }
    }

    async function handleOtpSubmit(event) {
        event.preventDefault();
        const code = [...document.querySelectorAll(".otp-digit")].map((input) => input.value).join("");
        if (code.length !== 6) return showAuthError("Vui lòng nhập đủ 6 chữ số.");
        hideAuthError();
        try {
            await completeAuthResponse(await fetch("/api/auth/verify", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code }),
            }));
        } catch (error) {
            console.error("OTP verification error:", error);
            showAuthError("Có lỗi kết nối tới máy chủ.");
        }
    }

    async function resendAuthCode() {
        const button = document.getElementById("resendCodeBtn");
        button.disabled = true;
        hideAuthError();
        try {
            const response = await fetch("/api/auth/resend", { method: "POST" });
            const data = await response.json();
            if (!response.ok) showAuthError(data.error || "Không thể gửi mã mới.");
            else {
                document.querySelectorAll(".otp-digit").forEach((input) => { input.value = ""; });
                await restoreAuthChallenge();
                showAuthNotice(data.message);
            }
        } catch (error) { showAuthError("Không thể kết nối để gửi mã."); }
        finally { button.disabled = Boolean(document.getElementById("resendCountdown").textContent); }
    }

    async function backToAuthLogin() {
        try {
            await fetch("/api/auth/cancel-challenge", { method: "POST" });
            authChallengeActive = false;
            if (otpCountdownTimer) clearInterval(otpCountdownTimer);
            hideAuthError();
            switchAuthTab("signin");
        } catch (error) {
            showAuthError("Không thể hủy xác minh lúc này. Vui lòng thử lại.");
        }
    }

    document.addEventListener("input", (event) => {
        if (!event.target.matches(".otp-digit")) return;
        const input = event.target;
        const digits = input.value.replace(/\D/g, "");
        if (digits.length > 1) {
            const start = Number(input.dataset.index);
            document.querySelectorAll(".otp-digit").forEach((field, offset) => {
                if (offset >= start && offset < start + 6) field.value = digits[offset - start] || "";
            });
            document.getElementById(`otpDigit${Math.min(start + digits.length, 5)}`)?.focus();
            return;
        }
        input.value = digits.slice(-1);
        if (input.value) document.getElementById(`otpDigit${Number(input.dataset.index) + 1}`)?.focus();
    });
    document.addEventListener("keydown", (event) => {
        if (!event.target.matches(".otp-digit") || event.key !== "Backspace") return;
        if (!event.target.value) document.getElementById(`otpDigit${Number(event.target.dataset.index) - 1}`)?.focus();
    });
    document.addEventListener("paste", (event) => {
        if (!event.target.matches(".otp-digit")) return;
        const digits = (event.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
        if (!digits) return;
        event.preventDefault();
        document.querySelectorAll(".otp-digit").forEach((input, index) => { input.value = digits[index] || ""; });
        document.getElementById(`otpDigit${Math.min(digits.length, 5)}`)?.focus();
    });
    // === KHỐI 25: Đăng xuất và dọn trạng thái giao diện ===
    async function handleSignOut() {
        if (!confirm("Bạn có chắc chắn muốn đăng xuất không?")) return;
        try {
            await fetch("/api/auth/signout", { method: "POST" });
        } catch (err) {
            console.error("Sign out error:", err);
        }
        currentUser = null;
        renderUserUI();
        localStorage.removeItem("thread_id");
        localStorage.removeItem("active_project_id");
        activeProjectId = "";
        activeProject = null;
        await loadProjects();
        await newChat();
    }

    // ==========================================
    // Profile Management & Avatar Upload
    // ==========================================
    // === KHỐI 26: Hồ sơ và ảnh đại diện ===
    function openProfileModal() {
        if (!currentUser) return;
        const modal = document.getElementById("profileModal");
        if (!modal) return;

        const err = document.getElementById("profileErrorBanner");
        const succ = document.getElementById("profileSuccessBanner");
        if (err) err.style.display = "none";
        if (succ) succ.style.display = "none";

        const uInput = document.getElementById("profileUsername");
        const fInput = document.getElementById("profileFullName");
        const eInput = document.getElementById("profileEmail");
        const cpInput = document.getElementById("profileCurrentPassword");
        const npInput = document.getElementById("profileNewPassword");

        if (uInput) uInput.value = currentUser.username || "";
        if (fInput) fInput.value = currentUser.full_name || currentUser.username || "";
        if (eInput) eInput.value = currentUser.email || "";
        if (cpInput) cpInput.value = "";
        if (npInput) npInput.value = "";

        updateProfileAvatarPreview(currentUser.avatar_url, currentUser.username);

        modal.style.display = "flex";
    }

    function closeProfileModal() {
        const modal = document.getElementById("profileModal");
        if (modal) modal.style.display = "none";
    }

    function updateProfileAvatarPreview(avatarUrl, username) {
        const img = document.getElementById("profileAvatarImg");
        const initial = document.getElementById("profileAvatarInitial");
        if (!img || !initial) return;

        const u = username || (currentUser ? currentUser.username : "U");
        const letter = (u || "U").charAt(0).toUpperCase();

        if (avatarUrl) {
            img.src = avatarUrl;
            img.style.display = "block";
            initial.style.display = "none";
        } else {
            img.style.display = "none";
            initial.textContent = letter;
            initial.style.display = "flex";
        }
    }

    async function handleAvatarFileSelected(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;

        const err = document.getElementById("profileErrorBanner");
        const succ = document.getElementById("profileSuccessBanner");
        if (err) err.style.display = "none";
        if (succ) succ.style.display = "none";

        if (file.size > 5 * 1024 * 1024) {
            if (err) {
                err.textContent = "Kích thước ảnh vượt quá 5MB. Vui lòng chọn ảnh nhỏ hơn.";
                err.style.display = "block";
            }
            return;
        }

        const uploadBtn = document.getElementById("uploadAvatarBtn");
        const oldHtml = uploadBtn ? uploadBtn.innerHTML : "";
        if (uploadBtn) {
            uploadBtn.disabled = true;
            uploadBtn.innerHTML = `<span>Đang tải lên...</span>`;
        }

        try {
            const formData = new FormData();
            formData.append("file", file);

            const res = await fetch("/api/auth/avatar", {
                method: "POST",
                body: formData
            });
            const data = await res.json();

            if (!res.ok || !data.success) {
                throw new Error(data.error || "Tải ảnh đại diện thất bại.");
            }

            currentUser = data.user || currentUser;
            if (data.avatar_url) {
                currentUser.avatar_url = data.avatar_url;
            }
            renderUserUI();
            updateProfileAvatarPreview(currentUser.avatar_url, currentUser.username);

            if (succ) {
                succ.textContent = "Đã cập nhật ảnh đại diện thành công!";
                succ.style.display = "block";
            }
        } catch (error) {
            if (err) {
                err.textContent = error.message;
                err.style.display = "block";
            }
        } finally {
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.innerHTML = oldHtml;
            }
            e.target.value = "";
        }
    }

    async function handleProfileSubmit(e) {
        e.preventDefault();
        const err = document.getElementById("profileErrorBanner");
        const succ = document.getElementById("profileSuccessBanner");
        if (err) err.style.display = "none";
        if (succ) succ.style.display = "none";

        const fullName = (document.getElementById("profileFullName").value || "").trim();
        const email = (document.getElementById("profileEmail").value || "").trim();
        const currentPassword = document.getElementById("profileCurrentPassword").value;
        const newPassword = document.getElementById("profileNewPassword").value;

        if (newPassword && !currentPassword) {
            if (err) {
                err.textContent = "Vui lòng nhập mật khẩu hiện tại để đổi mật khẩu mới.";
                err.style.display = "block";
            }
            return;
        }

        const submitBtn = document.getElementById("profileSubmitBtn");
        const oldHtml = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<span>Đang lưu...</span>`;

        try {
            const res = await fetch("/api/auth/profile", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    full_name: fullName,
                    email: email,
                    current_password: currentPassword || undefined,
                    new_password: newPassword || undefined
                })
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                throw new Error(data.error || "Cập nhật thông tin thất bại.");
            }

            currentUser = data.user || currentUser;
            renderUserUI();

            document.getElementById("profileCurrentPassword").value = "";
            document.getElementById("profileNewPassword").value = "";

            if (succ) {
                succ.textContent = "Cập nhật thông tin cá nhân thành công!";
                succ.style.display = "block";
            }
        } catch (error) {
            if (err) {
                err.textContent = error.message;
                err.style.display = "block";
            }
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = oldHtml;
        }
    }

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            const modal = document.getElementById("authModal");
            if (modal && modal.style.display !== "none") {
                closeAuthModal();
            }
            const pModal = document.getElementById("profileModal");
            if (pModal && pModal.style.display !== "none") {
                closeProfileModal();
            }
        }
    });


    // ==========================================
    // Cursor-driven motion (glow, parallax, magnets, card tilt)
    // ==========================================
    (function initCursorFX() {
        // Small surfaces: pointer-following spotlight (cheap, CSS-only rendering)
        document.addEventListener("pointermove", (e) => {
            const t = e.target.closest && e.target.closest(".history-item, .user-profile-card, .followup-suggestion, .welcome-pill, .input-wrapper");
            if (!t) return;
            const r = t.getBoundingClientRect();
            t.style.setProperty("--mx", (e.clientX - r.left) + "px");
            t.style.setProperty("--my", (e.clientY - r.top) + "px");
        }, { passive: true });

        // Keep Tab focus inside the auth modal while it is open
        document.addEventListener("keydown", (e) => {
            if (e.key !== "Tab") return;
            const modal = document.getElementById("authModal");
            if (!modal || modal.style.display === "none") return;
            const items = [...modal.querySelectorAll("button, input, a[href]")]
                .filter(el => !el.disabled && el.offsetParent !== null);
            if (!items.length) return;
            const first = items[0], last = items[items.length - 1];
            if (!modal.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
            else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        });

        const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
        const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (!fine || calm) return;

        const main = document.querySelector(".main");
        if (!main) return;
        const glow = document.createElement("div");
        glow.className = "cursor-glow";
        glow.setAttribute("aria-hidden", "true");
        // Three colour layers (blue / violet / cyan); each trails the pointer at its own speed
        const glowLayers = [
            { size: 860, k: 0.14, ox: 0,    oy: 0 },
            { size: 740, k: 0.085, ox: -300, oy: -240 },
            { size: 640, k: 0.055, ox: 260,  oy: 200 }
        ].map((cfg) => {
            const el = document.createElement("i");
            glow.appendChild(el);
            return Object.assign(cfg, { el, x: 0, y: 0 });
        });
        const ambient = main.querySelector(".ambient");
        main.insertBefore(glow, ambient ? ambient.nextSibling : main.firstChild);

        const MAGNETS = ".send-btn, .attach-btn, .mic-btn, .new-chat, .topbar-login-btn, .auth-open-btn, .welcome-pill, .auth-submit-btn";
        const S = { px: 0, py: 0, gx: 0, gy: 0, ga: 0, nx: 0, ny: 0, sx: 0, sy: 0, inside: false, down: false, raf: 0, first: true };
        const magnetState = new WeakMap();
        const cardState = new WeakMap();

        const ease = (obj, key, target, k, eps) => {
            obj[key] += (target - obj[key]) * k;
            if (Math.abs(target - obj[key]) < eps) { obj[key] = target; return false; }
            return true;
        };
        const shift = (el, fx, fy) => {
            if (el) el.style.translate = `${(S.sx * fx).toFixed(2)}px ${(S.sy * fy).toFixed(2)}px`;
        };

        function tick() {
            S.raf = 0;
            let busy = false;

            // 1) soft colour wash trailing the pointer; fades out as the pointer leaves .main
            const mr = main.getBoundingClientRect();
            const edge = Math.max(0, Math.min(1, (S.px - (mr.left - 160)) / 160));
            busy = ease(S, "ga", S.inside ? edge : 0, 0.09, 0.005) || busy;
            glowLayers.forEach((L) => {
                // offsets shift with the pointer's position, so the colours spread differently across the screen
                busy = ease(L, "x", S.px + S.nx * L.ox, L.k, 0.4) || busy;
                busy = ease(L, "y", S.py + S.ny * L.oy, L.k, 0.4) || busy;
                L.el.style.transform = `translate3d(${(L.x - mr.left - L.size / 2).toFixed(1)}px, ${(L.y - mr.top - L.size / 2).toFixed(1)}px, 0)`;
            });
            glow.style.opacity = S.ga.toFixed(3);

            // 2) depth parallax (-0.5 … 0.5 across the window)
            busy = ease(S, "sx", S.inside ? S.nx : 0, 0.07, 0.0006) || busy;
            busy = ease(S, "sy", S.inside ? S.ny : 0, 0.07, 0.0006) || busy;

            // ambient fields lean gently toward the pointer, moving together with the colour wash
            main.querySelectorAll(".ambient i").forEach((el, i) => shift(el, [46, 40, 54][i] || 40, [36, 30, 42][i] || 30));

            const welcome = document.getElementById("welcome");
            if (welcome && welcome.style.display !== "none") {
                shift(welcome.querySelector(".orbit-core"), 30, 26);
                shift(welcome.querySelector(".orbit-ring.r1"), -14, -12);
                shift(welcome.querySelector(".orbit-ring.r2"), 18, 15);
                shift(welcome.querySelector(".orbit-ring.r3"), 9, 8);
                shift(welcome.querySelector(".welcome-badge"), 8, 6);
                shift(welcome.querySelector(".welcome-title"), -6, -4);
            }
            const modal = document.getElementById("authModal");
            if (modal && modal.style.display !== "none") {
                const box = modal.querySelector(".auth-modal-container");
                if (box) {
                    box.style.setProperty("--mtx", (S.sx * 14).toFixed(2) + "px");
                    box.style.setProperty("--mty", (S.sy * 12).toFixed(2) + "px");
                }
            }

            // 3) magnetic buttons
            document.querySelectorAll(MAGNETS).forEach((el) => {
                let st = magnetState.get(el);
                if (!st) { st = { x: 0, y: 0 }; magnetState.set(el, st); }
                let tx = 0, ty = 0;
                if (S.inside && !el.disabled) {
                    const r = el.getBoundingClientRect();
                    const cx = r.left + r.width / 2 - st.x;
                    const cy = r.top + r.height / 2 - st.y;
                    const dx = S.px - cx, dy = S.py - cy;
                    const big = r.width > 140;
                    const reach = Math.max(r.width, r.height) / 2 + 34;
                    if (Math.hypot(dx, dy) < reach) {
                        const k = big ? 0.05 : 0.3, cap = big ? 4 : 11;
                        tx = Math.max(-cap, Math.min(cap, dx * k));
                        ty = Math.max(-cap, Math.min(cap, dy * k));
                    }
                }
                const moved = ease(st, "x", tx, 0.2, 0.05) | ease(st, "y", ty, 0.2, 0.05);
                if (moved) busy = true;
                el.style.translate = (st.x || st.y) ? `${st.x.toFixed(2)}px ${st.y.toFixed(2)}px` : "";
            });

            // 4) 3D tilt on suggestion cards
            document.querySelectorAll(".card").forEach((el) => {
                let st = cardState.get(el);
                if (!st) { st = { rx: 0, ry: 0, lift: 0, sc: 1 }; cardState.set(el, st); }
                let rx = 0, ry = 0, lift = 0, sc = 1;
                if (S.inside) {
                    const r = el.getBoundingClientRect();
                    if (S.px >= r.left && S.px <= r.right && S.py >= r.top && S.py <= r.bottom) {
                        const lx = (S.px - r.left) / r.width - 0.5;
                        const ly = (S.py - r.top) / r.height - 0.5;
                        rx = -ly * 9; ry = lx * 11; lift = -6;
                        if (S.down) sc = 0.985;
                    }
                }
                const moved = ease(st, "rx", rx, 0.16, 0.02) | ease(st, "ry", ry, 0.16, 0.02) |
                              ease(st, "lift", lift, 0.16, 0.05) | ease(st, "sc", sc, 0.2, 0.0005);
                if (moved) busy = true;
                el.style.setProperty("--rx", st.rx.toFixed(2) + "deg");
                el.style.setProperty("--ry", st.ry.toFixed(2) + "deg");
                el.style.setProperty("--lift", st.lift.toFixed(2) + "px");
                el.style.setProperty("--sc", st.sc.toFixed(4));
            });

            if (busy) S.raf = requestAnimationFrame(tick);
        }
        const kick = () => { if (!S.raf) S.raf = requestAnimationFrame(tick); };

        document.addEventListener("pointermove", (e) => {
            S.px = e.clientX; S.py = e.clientY;
            S.nx = e.clientX / window.innerWidth - 0.5;
            S.ny = e.clientY / window.innerHeight - 0.5;
            if (S.first) { glowLayers.forEach((L) => { L.x = S.px; L.y = S.py; }); S.first = false; }
            S.inside = true;
            kick();
        }, { passive: true });
        document.documentElement.addEventListener("pointerleave", () => { S.inside = false; S.down = false; kick(); });
        document.addEventListener("pointerdown", () => { S.down = true; kick(); });
        document.addEventListener("pointerup", () => { S.down = false; kick(); });
        document.addEventListener("pointercancel", () => { S.down = false; kick(); });
        window.addEventListener("blur", () => { S.inside = false; S.down = false; kick(); });
    })();

    // Khôi phục trạng thái sidebar đã lưu trên màn hình máy tính
    try {
        if (window.innerWidth > 768 && localStorage.getItem("sidebar_collapsed") === "true") {
            const sb = document.querySelector(".sidebar");
            if (sb) sb.classList.add("collapsed");
        }
    } catch (e) {}

    // Kh?i d?ng: ki?m tra auth tru?c, sau d� load d�ng tr?ng th�i
    // checkAuthStatus() c?n ho�n th�nh tru?c d? bi?t user d� dang nh?p chua
    // === KHỐI 27: Khôi phục trạng thái và khởi động trang ===
    captureProjectInvitationLink();
    window.addEventListener("hashchange", () => {
        if (!new URLSearchParams(location.hash.slice(1)).has("project-invite")) return;
        if (projectInvitationState) closeProjectInvitationModal(false);
        captureProjectInvitationLink();
        showProjectInvitationLink();
    });
    checkAuthStatus().then(async () => {
        restoreAuthChallenge();
        renderWelcome();
        await loadProjects();
        // Ch? restore thread cu n?u d� dang nh?p (guest kh�ng c� l?ch s?)
        if (currentUser && threadId) {
            await loadConversation(threadId, activeProjectId, { skipProjectReload: true });
        } else {
            await loadConversations();
        }
        await showProjectInvitationLink();
    });
