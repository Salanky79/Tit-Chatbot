/* admin.js: hành vi giao diện; tải đồng bộ tại vị trí script cũ.
 * Các khối giữ nguyên thứ tự để bảo toàn cascade và thứ tự khởi tạo.
 */

    const pageSize = 25;
    let userPage = 1, userTotalPages = 1;
    let documentPage = 1, documentTotalPages = 1;
    const conversationPageSize = 20, messagePageSize = 50;
    let selectedUser = null, selectedThreadId = null;
    let conversationPage = 1, conversationTotalPages = 1;
    let messagesPage = 1, messagesTotalPages = 1;
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const dateLabel = value => value ? new Date(value).toLocaleString() : "—";
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ---------- Toast ----------
    let toastTimer = null;
    const showMessage = value => {
        const el = document.getElementById("message");
        clearTimeout(toastTimer);
        if (!value) { el.classList.remove("show"); return; }
        el.textContent = value;
        el.classList.add("show");
        toastTimer = setTimeout(() => el.classList.remove("show"), 6000);
    };

    // ---------- Animated counters ----------
    function animateNumber(id, to) {
        const el = document.getElementById(id);
        const from = parseInt(el.dataset.value || "0", 10) || 0;
        el.dataset.value = to;
        if (reduceMotion || from === to) { el.textContent = Number(to).toLocaleString(); return; }
        const start = performance.now(), duration = 900;
        const step = now => {
            const t = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            el.textContent = Math.round(from + (to - from) * eased).toLocaleString();
            if (t < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    }

    // ---------- Skeleton rows while loading ----------
    function skeletonRows(cols, rows = 5) {
        const widths = [62, 78, 48, 70, 56, 66, 40];
        return Array.from({ length: rows }, (_, r) =>
            `<tr class="skel">${Array.from({ length: cols }, (_, c) => `<td><i style="--w:${widths[(r + c) % widths.length]}%"></i></td>`).join("")}</tr>`
        ).join("");
    }
    const emptyRow = (cols, text) => `<tr><td colspan="${cols}" class="empty"><div class="empty-state"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span>${text}</span></div></td></tr>`;

    // ---------- Confirm dialog (replaces window.confirm) ----------
    const dialog = { root: null, resolve: null, lastFocus: null };
    function confirmDialog({ title, text, okLabel = "Xác nhận", danger = true }) {
        const root = document.getElementById("dialog");
        const box = document.getElementById("dialogBox");
        dialog.root = root;
        dialog.lastFocus = document.activeElement;
        document.getElementById("dialogTitle").textContent = title;
        document.getElementById("dialogText").textContent = text;
        const ok = document.getElementById("dialogOk");
        ok.textContent = okLabel;
        ok.className = "action " + (danger ? "danger" : "ok");
        box.classList.toggle("safe", !danger);
        document.getElementById("dialogIcon").innerHTML = danger
            ? '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
            : '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
        root.hidden = false;
        requestAnimationFrame(() => root.classList.add("open"));
        document.getElementById("dialogCancel").focus();
        return new Promise(resolve => { dialog.resolve = resolve; });
    }
    function closeDialog(result) {
        const root = dialog.root;
        if (!root || !dialog.resolve) return;
        const resolve = dialog.resolve;
        dialog.resolve = null;
        root.classList.remove("open");
        setTimeout(() => { root.hidden = true; }, reduceMotion ? 0 : 260);
        if (dialog.lastFocus && dialog.lastFocus.focus) dialog.lastFocus.focus();
        resolve(result);
    }
    document.getElementById("dialogOk").addEventListener("click", () => closeDialog(true));
    document.getElementById("dialogCancel").addEventListener("click", () => closeDialog(false));
    document.getElementById("dialog").addEventListener("click", e => { if (e.target.id === "dialog") closeDialog(false); });
    document.addEventListener("keydown", e => {
        if (!dialog.resolve) return;
        if (e.key === "Escape") { e.preventDefault(); closeDialog(false); }
        if (e.key === "Tab") {
            const items = [document.getElementById("dialogCancel"), document.getElementById("dialogOk")];
            const i = items.indexOf(document.activeElement);
            e.preventDefault();
            items[(i + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
        }
    });

    // Cookie tit_session được browser gửi tự động; server xác thực role ở mọi API Admin.
    // === KHỐI 01: Gọi API quản trị với session cookie ===
    async function requestJson(url, options = {}) {
        const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Yêu cầu thất bại.");
        return data;
    }

    // === KHỐI 02: Danh sách và tìm kiếm tài khoản ===
    async function loadUsers(page = 1) {
        if (page < 1) return;
        if (userTotalPages > 0 && page > userTotalPages && page !== 1) return;
        const body = document.getElementById("usersBody");
        body.innerHTML = skeletonRows(6);
        try {
            const search = document.getElementById("userSearch").value.trim();
            const params = new URLSearchParams({ search: search, page: page, page_size: pageSize });
            const data = await requestJson(`/api/admin/users?${params}`);
            userPage = data.page;
            userTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
            if (!search) animateNumber("kpiUsers", data.total);

            // Escape các giá trị do user nhập trước khi ghép vào HTML của bảng.
            body.innerHTML = data.items.length ? data.items.map((user, i) => {
                const initial = escapeHtml((user.full_name || user.username || "?").trim().charAt(0).toUpperCase() || "?");
                const isAdmin = String(user.role).toLowerCase() === "admin";
                return `
                <tr class="row-in" style="--i:${i}"><td><div class="who"><span class="avatar">${initial}</span><div><div class="name">${escapeHtml(user.full_name)}</div><div class="muted">@${escapeHtml(user.username)}</div></div></div></td>
                <td>${escapeHtml(user.email)}</td><td><span class="badge ${isAdmin ? "admin" : ""}">${escapeHtml(user.role)}</span></td>
                <td><span class="badge ${user.is_active ? "on" : "off"}">${user.is_active ? "Hoạt động" : "Đã khóa"}</span></td><td>${dateLabel(user.created_at)}</td>
                <td><div class="user-actions">
                    <button class="action" data-action="view-user-conversations" data-user-id="${escapeHtml(user.id)}" data-username="${escapeHtml(user.username)}" data-full-name="${escapeHtml(user.full_name || user.username)}">Lịch sử chat</button>
                    <button class="action ${user.is_active ? "danger" : "ok"}" data-action="user-status" data-id="${escapeHtml(user.id)}" data-active="${!user.is_active}">${user.is_active ? "Khóa" : "Mở khóa"}</button>
                    <button class="action danger" data-action="delete-user" data-id="${escapeHtml(user.id)}" data-username="${escapeHtml(user.username)}" data-full-name="${escapeHtml(user.full_name || user.username)}">Xóa</button>
                </div></td></tr>`;
            }).join("") : emptyRow(6, "Không có người dùng phù hợp.");
            document.getElementById("usersPage").textContent = `${userPage} / ${userTotalPages} · ${data.total} tài khoản`;
            document.getElementById("userPrevBtn").disabled = (userPage <= 1);
            document.getElementById("userNextBtn").disabled = (userPage >= userTotalPages);
        } catch (error) {
            body.innerHTML = emptyRow(6, "Không tải được dữ liệu.");
            showMessage(error.message);
        }
    }

    // === KHỐI 03: Lịch sử chat và tin nhắn theo người dùng ===
    function openUserHistory(user) {
        selectedUser = user;
        selectedThreadId = null;
        document.getElementById("historyUserTitle").textContent = `Lịch sử hội thoại của: @${user.username} (${user.fullName || user.username})`;
        document.getElementById("conversationSearch").value = "";
        document.getElementById("messageViewer").hidden = true;
        document.getElementById("historyPanel").hidden = false;
        loadUserConversations(1);
        document.getElementById("historyPanel").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    }

    async function loadUserConversations(page = 1) {
        if (!selectedUser || page < 1) return;
        if (conversationTotalPages > 0 && page > conversationTotalPages && page !== 1) return;
        const requestedUserId = selectedUser.id;
        const list = document.getElementById("conversationsList");
        list.innerHTML = '<div class="muted">Đang tải hội thoại…</div>';
        try {
            const params = new URLSearchParams({
                search: document.getElementById("conversationSearch").value.trim(),
                page,
                page_size: conversationPageSize,
            });
            const data = await requestJson(`/api/admin/users/${encodeURIComponent(requestedUserId)}/conversations?${params}`);
            // Bỏ response cũ nếu Admin đã chọn user khác trong lúc request chạy.
            if (!selectedUser || selectedUser.id !== requestedUserId) return;
            conversationPage = data.page;
            conversationTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
            list.innerHTML = data.items.length ? data.items.map(conversation => `
                <button class="conversation-item" data-action="view-conversation" data-thread-id="${escapeHtml(conversation.thread_id)}" data-title="${escapeHtml(conversation.title || "New Chat")}" type="button">
                    <span class="conversation-title">${escapeHtml(conversation.title || "New Chat")}</span>
                    <span class="conversation-date">Cập nhật ${dateLabel(conversation.updated_at)}</span>
                </button>`).join("") : '<div class="empty-state muted">User này chưa có hội thoại.</div>';
            document.getElementById("conversationsPage").textContent = `${conversationPage} / ${conversationTotalPages} · ${data.total} hội thoại`;
            document.getElementById("conversationPrevBtn").disabled = conversationPage <= 1;
            document.getElementById("conversationNextBtn").disabled = conversationPage >= conversationTotalPages;
        } catch (error) {
            if (!selectedUser || selectedUser.id !== requestedUserId) return;
            list.innerHTML = '<div class="empty-state muted">Không tải được hội thoại.</div>';
            showMessage(error.message);
        }
    }

    async function loadConversationMessages(page = 1) {
        if (!selectedUser || !selectedThreadId || page < 1) return;
        if (messagesTotalPages > 0 && page > messagesTotalPages && page !== 1) return;
        const requestedUserId = selectedUser.id;
        const requestedThreadId = selectedThreadId;
        const messageList = document.getElementById("historyMessages");
        messageList.innerHTML = '<div class="muted">Đang tải tin nhắn…</div>';
        document.getElementById("messageViewer").hidden = false;
        try {
            const params = new URLSearchParams({ page, page_size: messagePageSize });
            const url = `/api/admin/users/${encodeURIComponent(requestedUserId)}/conversations/${encodeURIComponent(requestedThreadId)}/messages?${params}`;
            const data = await requestJson(url);
            // Không render message response nếu Admin đã đổi user hoặc thread.
            if (!selectedUser || selectedUser.id !== requestedUserId || selectedThreadId !== requestedThreadId) return;
            messagesPage = data.page;
            messagesTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
            document.getElementById("selectedConversationTitle").textContent = data.conversation.title || "New Chat";
            document.getElementById("selectedConversationDate").textContent = `${data.total} tin nhắn`;
            messageList.innerHTML = data.messages.length ? data.messages.map(message => {
                const isUser = message.role === "user";
                const roleLabel = isUser ? "User" : (message.role === "assistant" ? "Tit" : message.role);
                return `<article class="chat-bubble ${isUser ? "user" : "assistant"}">
                    <div class="chat-bubble-meta">${escapeHtml(roleLabel)} · ${dateLabel(message.created_at)}</div>
                    <div>${escapeHtml(message.content)}</div>
                </article>`;
            }).join("") : '<div class="empty-state muted">Hội thoại chưa có tin nhắn.</div>';
            const pager = document.getElementById("messagesPager");
            pager.hidden = messagesTotalPages <= 1;
            document.getElementById("messagesPageLabel").textContent = `${messagesPage} / ${messagesTotalPages} · ${data.total} tin nhắn`;
            document.getElementById("messagesPrevBtn").disabled = messagesPage <= 1;
            document.getElementById("messagesNextBtn").disabled = messagesPage >= messagesTotalPages;
            messageList.scrollTop = 0;
        } catch (error) {
            if (!selectedUser || selectedUser.id !== requestedUserId || selectedThreadId !== requestedThreadId) return;
            messageList.innerHTML = '<div class="empty-state muted">Không tải được tin nhắn.</div>';
            showMessage(error.message);
        }
    }

    function closeMessages() {
        selectedThreadId = null;
        document.getElementById("messageViewer").hidden = true;
    }

    function closeUserHistory() {
        selectedUser = null;
        closeMessages();
        document.getElementById("historyPanel").hidden = true;
    }

    // === KHỐI 04: Khóa, mở khóa và xóa tài khoản ===
    async function setUserStatus(userId, isActive) {
        const action = isActive ? "mở khóa" : "khóa";
        const ok = await confirmDialog({
            title: isActive ? "Mở khóa tài khoản" : "Khóa tài khoản",
            text: `Bạn muốn ${action} tài khoản này?`,
            okLabel: isActive ? "Mở khóa" : "Khóa",
            danger: !isActive,
        });
        if (!ok) return;
        try {
            await requestJson(`/api/admin/users/${encodeURIComponent(userId)}/status`, { method: "PATCH", body: JSON.stringify({ is_active: isActive }) });
            showMessage(""); await loadUsers(userPage);
        } catch (error) { showMessage(error.message); }
    }

    async function deleteUser(userId, username, fullName) {
        const ok = await confirmDialog({
            title: "Xóa vĩnh viễn tài khoản?",
            text: `Bạn có chắc muốn xóa @${username} (${fullName})? Lịch sử chat, bộ nhớ, tài liệu và file tải lên của tài khoản này cũng sẽ bị xóa.`,
            okLabel: "Xóa tài khoản",
            danger: true,
        });
        if (!ok) return;

        const visibleUserCount = document.querySelectorAll("#usersBody tr.row-in").length;
        const refreshPage = userPage > 1 && visibleUserCount === 1 ? userPage - 1 : userPage;
        try {
            await requestJson(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
            if (selectedUser?.id === userId) closeUserHistory();
            showMessage(`Đã xóa tài khoản @${username} và dữ liệu liên quan.`);
            await loadUsers(refreshPage);
        } catch (error) {
            showMessage(error.message);
        }
    }

    // === KHỐI 05: Danh sách và xóa tài liệu ===
    async function loadDocuments(page = 1) {
        if (page < 1) return;
        if (documentTotalPages > 0 && page > documentTotalPages && page !== 1) return;
        const body = document.getElementById("documentsBody");
        body.innerHTML = skeletonRows(7);
        try {
            const filters = {
                search: document.getElementById("docSearch").value.trim(),
                user_id: document.getElementById("docUser").value.trim(),
                file_type: document.getElementById("docType").value,
            };
            const params = new URLSearchParams({ ...filters, page: page, page_size: pageSize });
            const data = await requestJson(`/api/admin/documents?${params}`);
            documentPage = data.page;
            documentTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
            if (!filters.search && !filters.user_id && !filters.file_type) animateNumber("kpiDocs", data.total);

            body.innerHTML = data.items.length ? data.items.map((doc, i) => `
                <tr class="row-in" style="--i:${i}"><td class="name">${escapeHtml(doc.source)}</td>
                <td><span class="badge type">${escapeHtml(doc.file_type)}</span></td><td><span class="mono" title="${escapeHtml(doc.user_id || "Khách")}">${escapeHtml(doc.user_id || "Khách")}</span></td>
                <td><span class="mono" title="${escapeHtml(doc.thread_id || "—")}">${escapeHtml(doc.thread_id || "—")}</span></td><td>${doc.chunks}</td><td>${dateLabel(doc.uploaded_at)}</td>
                <td><button class="action danger" data-action="remove-document" data-id="${escapeHtml(doc.id)}">Xóa</button></td></tr>`).join("") : emptyRow(7, "Không có tài liệu phù hợp.");
            document.getElementById("documentsPage").textContent = `${documentPage} / ${documentTotalPages} · ${data.total} tài liệu`;
            document.getElementById("docPrevBtn").disabled = (documentPage <= 1);
            document.getElementById("docNextBtn").disabled = (documentPage >= documentTotalPages);
        } catch (error) {
            body.innerHTML = emptyRow(7, "Không tải được dữ liệu.");
            showMessage(error.message);
        }
    }

    async function removeDocument(documentId) {
        // Xóa dữ liệu là thao tác không thể hoàn tác từ UI nên luôn hỏi xác nhận.
        const ok = await confirmDialog({
            title: "Xóa tài liệu",
            text: "Xóa tài liệu này khỏi ChromaDB và xóa file tải lên nếu có đường dẫn lưu?",
            okLabel: "Xóa",
            danger: true,
        });
        if (!ok) return;
        try {
            await requestJson(`/api/admin/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
            showMessage(""); await loadDocuments(documentPage);
        } catch (error) { showMessage(error.message); }
    }

    // Event delegation giữ một handler cho các nút được tạo lại sau mỗi lần tải bảng.
    document.addEventListener("click", event => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        if (button.dataset.action === "user-status") {
            setUserStatus(button.dataset.id, button.dataset.active === "true");
        } else if (button.dataset.action === "delete-user") {
            deleteUser(
                button.dataset.id,
                button.dataset.username,
                button.dataset.fullName,
            );
        } else if (button.dataset.action === "view-user-conversations") {
            openUserHistory({
                id: button.dataset.userId,
                username: button.dataset.username,
                fullName: button.dataset.fullName,
            });
        } else if (button.dataset.action === "view-conversation") {
            selectedThreadId = button.dataset.threadId;
            loadConversationMessages(1);
        } else if (button.dataset.action === "close-messages") {
            closeMessages();
        } else if (button.dataset.action === "close-user-history") {
            closeUserHistory();
        } else if (button.dataset.action === "remove-document") {
            removeDocument(button.dataset.id);
        }
    });

    // ---------- Logo: little hop on click ----------
    const logoEl = document.getElementById("logo");
    logoEl.addEventListener("click", () => {
        if (reduceMotion) return;
        logoEl.classList.remove("hop");
        void logoEl.offsetWidth;
        logoEl.classList.add("hop");
    });

    // ---------- Cursor-driven light ----------
    (function initCursorFX() {
        // border light on panels / KPI cards follows the pointer
        document.addEventListener("pointermove", e => {
            const t = e.target.closest && e.target.closest(".panel, .kpi");
            if (!t) return;
            const r = t.getBoundingClientRect();
            t.style.setProperty("--mx", (e.clientX - r.left) + "px");
            t.style.setProperty("--my", (e.clientY - r.top) + "px");
        }, { passive: true });

        const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
        if (!fine || reduceMotion) return;

        const glow = document.getElementById("cursorGlow");
        const layers = [
            { el: glow.children[0], size: 780, k: 0.12, ox: 0, oy: 0, x: 0, y: 0 },
            { el: glow.children[1], size: 640, k: 0.07, ox: -260, oy: -200, x: 0, y: 0 },
        ];
        const S = { px: 0, py: 0, nx: 0, ny: 0, a: 0, inside: false, first: true, raf: 0 };
        const ease = (o, k, target, rate, eps) => { o[k] += (target - o[k]) * rate; if (Math.abs(target - o[k]) < eps) { o[k] = target; return false; } return true; };

        function tick() {
            S.raf = 0;
            let busy = ease(S, "a", S.inside ? 1 : 0, 0.09, 0.005);
            layers.forEach(L => {
                busy = ease(L, "x", S.px + S.nx * L.ox, L.k, 0.4) || busy;
                busy = ease(L, "y", S.py + S.ny * L.oy, L.k, 0.4) || busy;
                L.el.style.transform = `translate3d(${(L.x - L.size / 2).toFixed(1)}px, ${(L.y - L.size / 2).toFixed(1)}px, 0)`;
            });
            glow.style.opacity = S.a.toFixed(3);
            if (busy) S.raf = requestAnimationFrame(tick);
        }
        const kick = () => { if (!S.raf) S.raf = requestAnimationFrame(tick); };
        document.addEventListener("pointermove", e => {
            S.px = e.clientX; S.py = e.clientY;
            S.nx = e.clientX / innerWidth - 0.5; S.ny = e.clientY / innerHeight - 0.5;
            if (S.first) { layers.forEach(L => { L.x = S.px; L.y = S.py; }); S.first = false; }
            S.inside = true; kick();
        }, { passive: true });
        document.documentElement.addEventListener("pointerleave", () => { S.inside = false; kick(); });
        window.addEventListener("blur", () => { S.inside = false; kick(); });
    })();

    loadUsers();
    loadDocuments();
