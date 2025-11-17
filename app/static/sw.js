// static/sw.js
self.addEventListener('push', event => {
    const data = event.data.json();
    const options = {
        body: data.body,
        icon: '/static/favicon.ico',
        badge: '/static/badge.png'
    };
    event.waitUntil(self.registration.showNotification(data.title, options));
});