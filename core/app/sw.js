/* Keeps the app shell so it opens instantly and shows a page when offline. Data calls always go to the server. */
var V="hub-shell-v1";
self.addEventListener("install",function(e){self.skipWaiting();e.waitUntil(caches.open(V).then(function(c){return c.add("./")}).catch(function(){}))});
self.addEventListener("activate",function(e){e.waitUntil(caches.keys().then(function(k){return Promise.all(k.filter(function(x){return x!==V}).map(function(x){return caches.delete(x)}))}).then(function(){return self.clients.claim()}))});
self.addEventListener("fetch",function(e){
  var r=e.request,u=new URL(r.url);
  if(r.method!=="GET"||u.origin!==location.origin)return;
  if(r.mode==="navigate"){
    e.respondWith(fetch(r).then(function(x){var y=x.clone();caches.open(V).then(function(c){c.put("./",y)});return x}).catch(function(){return caches.match("./")}));
  }
});
