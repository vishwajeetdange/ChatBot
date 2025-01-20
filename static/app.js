document.addEventListener('DOMContentLoaded', () => {
    const chatContainer = document.getElementById('chatContainer');
    const chatForm = document.getElementById('chatForm');
    const userInput = document.getElementById('userInput');
    const pdfUploadForm = document.getElementById('pdfUploadForm');
    const clearHistoryBtn = document.getElementById('clearHistory');
    const uploadToast = new bootstrap.Toast(document.getElementById('uploadToast'));

    // PDF Upload Handler
    pdfUploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pdfFile = document.getElementById('pdfFileInput').files[0];
        
        if (!pdfFile) {
            showToast('Please select a PDF file', 'danger');
            return;
        }

        const formData = new FormData();
        formData.append('pdf_file', pdfFile);

        try {
            const response = await fetch('/layout', {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                showToast('PDF uploaded successfully!', 'success');
                pdfUploadForm.reset();
            } else {
                showToast('Upload failed. Please try again.', 'danger');
            }
        } catch (error) {
            console.error('Upload error:', error);
            showToast('Network error. Please try again.', 'danger');
        }
    });

    // Chat Interaction Handler
    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const message = userInput.value.trim();

        if (!message) return;

        // Add user message
        appendMessage(message, 'user');
        userInput.value = '';

        try {
            const response = await fetch('/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `user_input=${encodeURIComponent(message)}`
            });

            const responseText = await response.text();
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = responseText;
            
            // Extract assistant's message from the returned HTML
            const assistantMessage = tempDiv.querySelector('.chat-message.assistant-message');
            
            if (assistantMessage) {
                const messageText = assistantMessage.querySelector('.message-text').textContent;
                const messageImages = assistantMessage.querySelectorAll('.chat-image');

                appendMessage(messageText, 'assistant', Array.from(messageImages).map(img => img.src));
            }
        } catch (error) {
            console.error('Chat error:', error);
            appendMessage('Sorry, there was an error processing your request.', 'assistant');
        }
    });

    // Clear History Handler
    clearHistoryBtn.addEventListener('click', async () => {
        try {
            await fetch('/clear');
            chatContainer.innerHTML = '';
            showToast('Chat history cleared', 'success');
        } catch (error) {
            showToast('Failed to clear history', 'danger');
        }
    });

    // Utility Functions
    function appendMessage(message, type, images = []) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('chat-message', `${type}-message`, 'd-flex', 'flex-column');
        
        const textSpan = document.createElement('span');
        textSpan.classList.add('message-text');
        textSpan.textContent = message;
        messageDiv.appendChild(textSpan);

        // Add images if present
        images.forEach(imageSrc => {
            const img = document.createElement('img');
            img.src = imageSrc;
            img.classList.add('chat-image', 'animate__animated', 'animate__fadeIn');
            img.addEventListener('click', () => openImageModal(imageSrc));
            messageDiv.appendChild(img);
        });

        chatContainer.appendChild(messageDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function showToast(message, type = 'info') {
        const toastBody = document.querySelector('#uploadToast .toast-body');
        toastBody.textContent = message;
        toastBody.className = `toast-body text-${type}`;
        uploadToast.show();
    }

    function openImageModal(src) {
        // Implement image zoom modal if needed
        const modal = document.createElement('div');
        modal.classList.add('modal', 'fade');
        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered modal-xl">
                <div class="modal-content">
                    <img src="${src}" class="img-fluid">
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        new bootstrap.Modal(modal).show();
    }
});