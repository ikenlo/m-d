package ua.md.connect;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

/**
 * MDS Connect для Android: один екран з веб-панеллю салону.
 *
 * - Сторінки нашого сервера відкриваються всередині застосунку; усе інше (телефон, Telegram,
 *   карти, вхід у Google) — у відповідному застосунку чи браузері.
 * - Вхід зберігається між запусками (localStorage WebView).
 * - Без інтернету — екран «Немає з'єднання» з кнопкою «Спробувати ще».
 * - Вибір файлу (знімок екрана в баг-репорті) працює через системний вибір файлів.
 * - Оплата карткою: панель викликає MDSAndroid.startTerminal(deeplink) → відкривається «Термінал» ПриватБанку
 *   (NFC), після оплати результат повертається в панель (window.mdsTerminalResult). Фінальний статус сервер
 *   завжди перевіряє сам у банку — відповідь застосунку лише сигнал «перевір».
 */
public class MainActivity extends Activity {
    private static final int FILE_REQUEST = 1001;
    private static final int TERMINAL_REQUEST = 1002;
    private static final String TERMINAL_PACKAGE = "ua.privatbank.pterminal";
    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private String appHost;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        appHost = Uri.parse(BuildConfig.APP_URL).getHost();

        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#14392C"));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#F4F3EE"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " MDSConnectApp/" + BuildConfig.VERSION_NAME + " Android");

        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (isOurs(u)) {
                    return false;
                }
                openExternal(u);
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showOffline(view);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_REQUEST);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.addJavascriptInterface(new TerminalBridge(), "MDSAndroid");

        // файли для завантаження (напр. звіти) — відкриваємо в браузері
        web.setDownloadListener((url, ua, cd, mime, len) -> openExternal(Uri.parse(url)));

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            web.loadUrl(BuildConfig.APP_URL);
        }
    }

    private boolean isOurs(Uri u) {
        return u != null && "https".equals(u.getScheme()) && appHost != null && appHost.equalsIgnoreCase(u.getHost());
    }

    private void openExternal(Uri u) {
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase();
        // лише безпечні схеми: веб, телефон, пошта, Telegram, карти
        if (!(scheme.equals("https") || scheme.equals("http") || scheme.equals("tel") || scheme.equals("mailto")
                || scheme.equals("tg") || scheme.equals("geo") || scheme.equals("intent"))) {
            return;
        }
        try {
            Intent i = scheme.equals("intent") ? Intent.parseUri(u.toString(), Intent.URI_INTENT_SCHEME)
                    : new Intent(Intent.ACTION_VIEW, u);
            i.addCategory(Intent.CATEGORY_BROWSABLE);
            i.setComponent(null);
            i.setSelector(null);
            startActivity(i);
        } catch (Exception ignored) {
            // немає застосунку для такого посилання — нічого не робимо
        }
    }

    private void showOffline(WebView view) {
        String html = "<!doctype html><html lang='uk'><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1'>"
                + "<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
                + "background:#14392C;color:#F5F1E6;font-family:sans-serif;text-align:center}"
                + ".b{padding:32px}.m{width:72px;height:72px;margin:0 auto 20px;border-radius:18px;"
                + "background:linear-gradient(135deg,#D9B872,#A9833F);color:#14392C;font-weight:700;"
                + "font-size:22px;line-height:72px}h1{font-size:22px;margin:0 0 8px}p{opacity:.8;margin:0 0 24px}"
                + "a{display:inline-block;padding:14px 28px;border-radius:12px;background:#C9A45C;color:#14392C;"
                + "font-weight:600;text-decoration:none}</style></head><body><div class='b'><div class='m'>M&amp;D</div>"
                + "<h1>Немає з'єднання</h1><p>Перевірте інтернет і спробуйте ще раз.</p>"
                + "<a href='" + BuildConfig.APP_URL + "'>Спробувати ще</a></div>"
                // автоповтор: щойно з'єднання повернеться, панель відкриється сама
                + "<script>setTimeout(function(){location.href='" + BuildConfig.APP_URL + "'},8000);"
                + "window.addEventListener('online',function(){location.href='" + BuildConfig.APP_URL + "'});</script>"
                + "</body></html>";
        view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    /** Міст для веб-панелі: лише запуск «Термінала» за deeplink від нашого сервера. */
    private class TerminalBridge {
        @JavascriptInterface
        public boolean available() {
            return true;
        }

        @JavascriptInterface
        public void startTerminal(final String url) {
            runOnUiThread(() -> {
                Uri u = Uri.parse(url == null ? "" : url);
                // приймаємо лише deeplink «Термінала» ПриватБанку
                if (!"nfcterminal".equals(u.getScheme()) || !"executor".equals(u.getHost())) {
                    deliverTerminalResult("{\"result\":false,\"error\":\"bad_link\"}");
                    return;
                }
                try {
                    startActivityForResult(new Intent(Intent.ACTION_VIEW, u), TERMINAL_REQUEST);
                } catch (ActivityNotFoundException e) {
                    deliverTerminalResult("{\"result\":false,\"error\":\"not_installed\"}");
                    openExternal(Uri.parse("https://play.google.com/store/apps/details?id=" + TERMINAL_PACKAGE));
                }
            });
        }
    }

    private void deliverTerminalResult(String json) {
        if (web == null) {
            return;
        }
        String arg = JSONObject.quote(json == null ? "" : json);
        web.evaluateJavascript("window.mdsTerminalResult && window.mdsTerminalResult(" + arg + ")", null);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == TERMINAL_REQUEST) {
            String r = data != null ? data.getStringExtra("result") : null;
            deliverTerminalResult(r != null ? r : "{\"result\":false,\"error\":\"no_result\"}");
            return;
        }
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
        web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.setVisibility(View.GONE);
            web.destroy();
        }
        super.onDestroy();
    }
}
