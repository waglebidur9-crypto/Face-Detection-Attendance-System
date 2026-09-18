/**
 * Face Recognition Attendance System - Main Web Client Logic
 */

document.addEventListener('DOMContentLoaded', () => {
    // Initialize UI tooltips if Bootstrap is present
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));
    }

    // Auto-dismiss alert banners after 5 seconds
    const alerts = document.querySelectorAll('.alert-dismissible');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = bootstrap.Alert.getInstance(alert) || new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
});

/**
 * Global helper to display dynamic status notifications
 */
function showStatusToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed bottom-0 end-0 p-3';
        container.style.zIndex = '1100';
        document.body.appendChild(container);
    }

    const toastId = 'toast-' + Date.now();
    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white bg-${type === 'error' ? 'danger' : type} border-0 show" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', toastHtml);

    setTimeout(() => {
        const el = document.getElementById(toastId);
        if (el) el.remove();
    }, 4000);
}

/**
 * Updates Dashboard UI Badges ("Face Not Recognized" / "Attendance Already Marked")
 */
function updateDashboardBadges(faces) {
    const badgeFace = document.getElementById('badge-face');
    const badgeAttendance = document.getElementById('badge-attendance');
    if (!badgeFace || !badgeAttendance) return;

    if (!faces || faces.length === 0) {
        badgeFace.className = "status-badge badge-danger";
        badgeFace.innerHTML = `<i class="bi bi-x-circle-fill"></i> Face Not Recognized`;
        badgeAttendance.className = "status-badge badge-warning d-none";
        return;
    }

    const recognized = faces.find(f => f.student_id !== null);
    if (recognized) {
        badgeFace.className = "status-badge badge-success";
        badgeFace.innerHTML = `<i class="bi bi-check-circle-fill"></i> Recognized: ${recognized.name}`;
        
        if (!recognized.marked) {
            badgeAttendance.className = "status-badge badge-warning";
            badgeAttendance.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> Attendance Already Marked`;
        } else {
            badgeAttendance.className = "status-badge badge-success";
            badgeAttendance.innerHTML = `<i class="bi bi-check2-all"></i> Attendance Recorded`;
        }
    } else {
        badgeFace.className = "status-badge badge-danger";
        badgeFace.innerHTML = `<i class="bi bi-x-circle-fill"></i> Face Not Recognized`;
        badgeAttendance.className = "status-badge badge-warning d-none";
    }
}

/**
 * Camera Stream Controller Class
 */
class CameraHandler {
    constructor(videoElementId, overlayCanvasId = null) {
        this.video = document.getElementById(videoElementId);
        this.canvas = overlayCanvasId ? document.getElementById(overlayCanvasId) : null;
        this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
        this.stream = null;
        this.isProcessing = false;
        this.intervalId = null;
    }

    async startCamera() {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
            });
            this.video.srcObject = this.stream;
            await this.video.play();
            return true;
        } catch (err) {
            console.error("Camera Access Error:", err);
            showStatusToast("Unable to access camera: " + err.message, "danger");
            return false;
        }
    }

    stopCamera() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.video.srcObject = null;
        }
    }

    captureFrameBase64(quality = 0.65) {
        if (!this.video || this.video.videoWidth === 0) return null;
        
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = this.video.videoWidth;
        tempCanvas.height = this.video.videoHeight;
        const tempCtx = tempCanvas.getContext('2d');
        tempCtx.drawImage(this.video, 0, 0, tempCanvas.width, tempCanvas.height);
        
        return tempCanvas.toDataURL('image/jpeg', quality);
    }

    drawDetections(faces) {
        if (!this.canvas || !this.ctx) return;

        // Sync canvas resolution with internal video dimensions
        if (this.canvas.width !== this.video.videoWidth) {
            this.canvas.width = this.video.videoWidth;
            this.canvas.height = this.video.videoHeight;
        }

        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        faces.forEach(face => {
            const [x, y, w, h] = face.box;
            const isKnown = face.student_id !== null;
            const color = isKnown ? '#10b981' : '#ef4444'; // Green for known, Red for unknown

            // Render bounding box
            this.ctx.strokeStyle = color;
            this.ctx.lineWidth = 3;
            this.ctx.strokeRect(x, y, w, h);

            // Render text label
            const label = isKnown ? `${face.name} (${face.score}%)` : 'Unknown';
            this.ctx.font = 'bold 14px Segoe UI, sans-serif';
            const textWidth = this.ctx.measureText(label).width;

            this.ctx.fillStyle = color;
            this.ctx.fillRect(x, y > 30 ? y - 28 : y, textWidth + 12, 25);

            this.ctx.fillStyle = '#ffffff';
            this.ctx.fillText(label, x + 6, y > 30 ? y - 10 : y + 17);
        });
    }
}

/**
 * Real-Time Recognition Stream Loop
 */
function initRealtimeRecognition(videoId, canvasId, statusBoxId) {
    const handler = new CameraHandler(videoId, canvasId);
    const statusBox = statusBoxId ? document.getElementById(statusBoxId) : null;

    handler.startCamera().then(success => {
        if (!success) return;

        handler.intervalId = setInterval(async () => {
            if (handler.isProcessing) return;
            handler.isProcessing = true;

            const base64Image = handler.captureFrameBase64(0.65);
            if (!base64Image) {
                handler.isProcessing = false;
                return;
            }

            try {
                const response = await fetch('/api/process_frame', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image: base64Image })
                });

                const data = await response.json();

                if (data.status === 'success' && data.faces.length > 0) {
                    handler.drawDetections(data.faces);
                    updateDashboardBadges(data.faces);

                    // Optional legacy status text box handler
                    data.faces.forEach(face => {
                        if (face.marked && statusBox) {
                            statusBox.className = "alert alert-success mt-3 shadow-sm";
                            statusBox.innerHTML = `<strong>Attendance Recorded:</strong> ${face.name} (${face.student_id})`;
                            statusBox.classList.remove('d-none');
                            showStatusToast(`Attendance marked for ${face.name}`, "success");
                        }
                    });
                } else {
                    if (handler.ctx) {
                        handler.ctx.clearRect(0, 0, handler.canvas.width, handler.canvas.height);
                    }
                    updateDashboardBadges([]);
                }
            } catch (err) {
                console.error("Frame recognition pipeline error:", err);
            } finally {
                handler.isProcessing = false;
            }
        }, 700); // Frame capture interval: 700ms
    });
}