"use strict";

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {  } },
  del(k) { try { localStorage.removeItem(k); } catch (_) {  } },
};
const sessionStore = {
  get(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch (_) {  } },
  del(k) { try { sessionStorage.removeItem(k); } catch (_) {  } },
};
