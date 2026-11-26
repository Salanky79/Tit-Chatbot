/* Presentation helpers only. API calls and permissions stay in the page. */
const UI = (() => {
    let toastTimer;
    let dialogQueue = Promise.resolve();

    function notify(message) {
        let region = document.getElementById('uiToasts');
        if (!region) {
            region = document.createElement('div');
            region.id = 'uiToasts';
            region.className = 'ui-toast-region';
            region.setAttribute('role', 'status');
            region.setAttribute('aria-live', 'polite');
            document.body.append(region);
        }
        clearTimeout(toastTimer);
        const toast = document.createElement('div');
        toast.className = 'ui-toast';
        const text = document.createElement('span');
        text.textContent = String(message);
        const close = document.createElement('button');
        close.type = 'button';
        close.textContent = '×';
        close.setAttribute('aria-label', 'Đóng thông báo');
        close.onclick = () => toast.remove();
        toast.append(text, close);
        region.replaceChildren(toast);
        const dismiss = () => { toastTimer = setTimeout(() => toast.remove(), 9000); };
        toast.addEventListener('mouseenter', () => clearTimeout(toastTimer));
        toast.addEventListener('focusin', () => clearTimeout(toastTimer));
        toast.addEventListener('mouseleave', dismiss);
        toast.addEventListener('focusout', dismiss);
        dismiss();
    }

    function form(options) {
        const open = () => new Promise(resolve => {
            const previous = document.activeElement;
            const dialog = document.createElement('dialog');
            dialog.className = 'ui-dialog';
            dialog.setAttribute('aria-labelledby', 'uiDialogTitle');
            dialog.setAttribute('aria-describedby', 'uiDialogDescription');
            const formEl = document.createElement('form');
            const title = document.createElement('h2');
            title.id = 'uiDialogTitle';
            title.textContent = options.title || 'Cập nhật';
            const description = document.createElement('p');
            description.id = 'uiDialogDescription';
            description.className = 'ui-dialog-description';
            description.textContent = options.description || '';
            formEl.append(title, description);
            for (const field of options.fields || []) {
                const wrap = document.createElement('div');
                wrap.className = 'ui-field';
                const label = document.createElement('label');
                label.htmlFor = `uiField-${field.name}`;
                label.textContent = field.label;
                const input = document.createElement(field.options ? 'select' : field.multiline ? 'textarea' : 'input');
                input.name = field.name;
                input.id = label.htmlFor;
                input.required = !!field.required;
                if (field.maxLength) input.maxLength = field.maxLength;
                for (const [value, text] of field.options || []) {
                    input.add(new Option(text, value));
                }
                input.value = field.value ?? '';
                wrap.append(label, input);
                if (field.help) {
                    const help = document.createElement('small');
                    help.id = `${input.id}-help`;
                    help.textContent = field.help;
                    input.setAttribute('aria-describedby', help.id);
                    wrap.append(help);
                }
                formEl.append(wrap);
            }
            const actions = document.createElement('div');
            actions.className = 'ui-dialog-actions';
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'ui-button';
            cancel.textContent = 'Hủy';
            const submit = document.createElement('button');
            submit.type = 'submit';
            submit.className = `ui-button ${options.danger ? 'danger' : 'primary'}`;
            submit.textContent = options.submit || 'Lưu thay đổi';
            actions.append(cancel, submit);
            formEl.append(actions);
            dialog.append(formEl);
            document.body.append(dialog);
            let result = null;
            cancel.onclick = () => dialog.close();
            formEl.onsubmit = event => {
                event.preventDefault();
                for (const input of formEl.querySelectorAll('[required]')) {
                    if (!input.value.trim()) {
                        input.setCustomValidity('Vui lòng nhập nội dung.');
                        input.reportValidity();
                        input.addEventListener('input', () => input.setCustomValidity(''), {once: true});
                        return;
                    }
                }
                result = Object.fromEntries(new FormData(formEl));
                dialog.close();
            };
            dialog.addEventListener('close', () => {
                dialog.remove();
                if (previous?.isConnected && !previous.closest('[inert]')) previous.focus();
                resolve(result);
            }, {once: true});
            dialog.showModal();
            (options.danger ? cancel : formEl.querySelector('input,textarea,select') || cancel).focus();
        });
        const pending = dialogQueue.then(open);
        dialogQueue = pending.catch(() => {});
        return pending;
    }

    function init() {
        const overlays = [...document.querySelectorAll('.auth-modal-overlay')];
        let current = null;
        let previousFocus = null;
        const backgrounds = [...document.querySelectorAll('body > main, body > aside, body > .skip-link')];
        const sidebar = document.querySelector('.sidebar');
        const main = document.querySelector('body > main');
        const mobile = matchMedia('(max-width: 768px)');
        let drawerOpen = false;
        const syncSidebar = () => {
            if (!sidebar) return;
            const open = mobile.matches && sidebar.classList.contains('open');
            const hidden = mobile.matches ? !open : sidebar.classList.contains('collapsed');
            const hadSidebarFocus = sidebar.contains(document.activeElement);
            sidebar.inert = !!current || hidden;
            sidebar.setAttribute('aria-hidden', String(hidden));
            if (main) main.inert = !!current || open;
            document.querySelectorAll('#sidebarToggle,#sidebarCollapseBtn').forEach(button => {
                button.setAttribute('aria-controls', sidebar.id);
                button.setAttribute('aria-expanded', String(!hidden));
            });
            if (!current && open && !drawerOpen) sidebar.querySelector('button')?.focus();
            if (!current && !open && drawerOpen) document.getElementById('sidebarToggle')?.focus();
            if (!current && hidden && hadSidebarFocus) document.getElementById('sidebarToggle')?.focus();
            drawerOpen = open;
        };
        const sync = () => {
            const next = overlays.find(el => getComputedStyle(el).display !== 'none') || null;
            if (next === current) return;
            if (!current && next) previousFocus = document.activeElement;
            current = next;
            if (current) {
                clearTimeout(toastTimer);
                document.getElementById('uiToasts')?.replaceChildren();
            }
            backgrounds.forEach(el => { el.inert = !!current; });
            syncSidebar();
            if (current) {
                const box = current.querySelector('[role="dialog"]');
                if (box) box.tabIndex = -1;
                const first = [...current.querySelectorAll('input,textarea,select,button')].find(el => !el.disabled && el.getClientRects().length);
                (first || box)?.focus();
            } else {
                if (previousFocus?.isConnected && previousFocus.getClientRects().length && !previousFocus.closest('[inert]')) previousFocus.focus();
                else document.getElementById('messageInput')?.focus();
                previousFocus = null;
            }
        };
        const observer = new MutationObserver(sync);
        overlays.forEach(el => observer.observe(el, {attributes: true, attributeFilter: ['style']}));
        document.addEventListener('keydown', event => {
            const target = event.target.closest('.user-profile-card[role="button"], .topbar-user-pill[role="button"]');
            if (target && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault(); target.click();
            }
            const menu = event.target.closest('.user-account-menu.open');
            if (menu && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                event.preventDefault();
                const items = [...menu.querySelectorAll('button')];
                const index = items.indexOf(document.activeElement);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
                items[next]?.focus();
            }
            if (menu && event.key === 'Escape') document.querySelector('.user-profile-card')?.focus();
        }, true);
        document.addEventListener('keydown', event => {
            if (document.querySelector('dialog[open]')) {
                if (event.key === 'Escape') event.stopImmediatePropagation();
                return;
            }
            const scope = current || (drawerOpen ? sidebar : null);
            if (!scope) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (current) current.querySelector('.auth-modal-backdrop')?.click();
                else sidebar.classList.remove('open');
            }
            if (event.key !== 'Tab') return;
            const items = [...scope.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')]
                .filter(el => !el.disabled && el.getClientRects().length);
            const first = items[0], last = items.at(-1);
            if (!first) return;
            if (!scope.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)) {
                event.preventDefault(); first.focus();
            } else if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last.focus();
            }
        }, true);
        // Existing dynamically rendered icon controls inherit their descriptive title.
        const labelControls = root => {
            root.querySelectorAll('button[title]:not([aria-label]),select[title]:not([aria-label])').forEach(el => el.setAttribute('aria-label', el.title));
        };
        labelControls(document);
        new MutationObserver(records => {
            for (const record of records) for (const node of record.addedNodes) {
                if (node.nodeType === 1) {
                    if (node.matches('button[title]:not([aria-label])')) node.setAttribute('aria-label', node.title);
                    labelControls(node);
                }
            }
        }).observe(document.body, {childList: true, subtree: true});
        if (sidebar) {
            new MutationObserver(syncSidebar).observe(sidebar, {attributes: true, attributeFilter: ['class']});
            mobile.addEventListener('change', syncSidebar);
            syncSidebar();
        }
        sync();
    }
    document.addEventListener('DOMContentLoaded', init);
    return {
        notify, form,
        confirm: async description => (await form({title: 'Xác nhận thao tác', description, danger: true, submit: 'Xác nhận'})) !== null,
        prompt: async (label, value = '') => {
            const result = await form({title: 'Cập nhật thông tin', fields: [{name: 'value', label, value}], submit: 'Tiếp tục'});
            return result ? result.value : null;
        }
    };
})();
