package ua.md.connect;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothSocket;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Base64;
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

import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

public class MainActivity extends Activity {
    private static final int FILE_REQUEST = 1001;
    private static final int TERMINAL_REQUEST = 1002;
    private static final String TERMINAL_PACKAGE = "ua.privatbank.pterminal";
    private static final int BT_PERMISSION_REQUEST = 1003;
    private static final UUID SPP_UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");
    private static final int MAX_PRINT_BYTES = 256 * 1024;
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
                + "<script>setTimeout(function(){location.href='" + BuildConfig.APP_URL + "'},8000);"
                + "window.addEventListener('online',function(){location.href='" + BuildConfig.APP_URL + "'});</script>"
                + "</body></html>";
        view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private class TerminalBridge {
        @JavascriptInterface
        public boolean available() {
            return true;
        }

        @JavascriptInterface
        public void startTerminal(final String url) {
            runOnUiThread(() -> {
                Uri u = Uri.parse(url == null ? "" : url);
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

        @JavascriptInterface
        public void printWifi(final String ip, final int port, final String base64Data) {
            runOnUiThread(() -> {
                if (!pageIsOurs()) {
                    return;
                }
                final byte[] data = decode(base64Data);
                if (data == null) {
                    deliverPrintResult(false, "Немає даних для друку");
                    return;
                }
                new Thread(() -> printOverWifi(ip, port, data), "mds-print-wifi").start();
            });
        }

        @JavascriptInterface
        public void printBluetooth(final String base64Data, final String deviceName) {
            runOnUiThread(() -> {
                if (!pageIsOurs()) {
                    return;
                }
                final byte[] data = decode(base64Data);
                if (data == null) {
                    deliverPrintResult(false, "Немає даних для друку");
                    return;
                }
                if (Build.VERSION.SDK_INT >= 31
                        && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, BT_PERMISSION_REQUEST);
                    deliverPrintResult(false, "Дозвольте доступ до Bluetooth і натисніть «Друк» ще раз");
                    return;
                }
                new Thread(() -> printOverBluetooth(deviceName, data), "mds-print-bt").start();
            });
        }
    }

    private boolean pageIsOurs() {
        return web != null && web.getUrl() != null && isOurs(Uri.parse(web.getUrl()));
    }

    private static byte[] decode(String base64Data) {
        if (base64Data == null || base64Data.isEmpty() || base64Data.length() > MAX_PRINT_BYTES * 2) {
            return null;
        }
        try {
            byte[] b = Base64.decode(base64Data, Base64.DEFAULT);
            return b.length == 0 || b.length > MAX_PRINT_BYTES ? null : b;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    // Лише принтер у локальній мережі салону (10.x, 172.16–31.x, 192.168.x) — сторінка не може слати дані будь-куди.
    private void printOverWifi(String ip, int port, byte[] data) {
        InetAddress addr;
        try {
            addr = InetAddress.getByName(ip == null ? "" : ip.trim());
        } catch (Exception e) {
            deliverPrintResult(false, "Невірна IP-адреса принтера");
            return;
        }
        if (!(addr instanceof Inet4Address) || !addr.isSiteLocalAddress() || port < 1 || port > 65535) {
            deliverPrintResult(false, "Принтер має бути в локальній мережі салону");
            return;
        }
        try (Socket sock = new Socket()) {
            sock.connect(new InetSocketAddress(addr, port), 5000);
            sock.setSoTimeout(10000);
            OutputStream out = sock.getOutputStream();
            out.write(data);
            out.flush();
            deliverPrintResult(true, "Чек надруковано");
        } catch (Exception e) {
            deliverPrintResult(false, "Принтер Wi-Fi недоступний (" + ip + ":" + port + ")");
        }
    }

    @SuppressLint("MissingPermission")
    private void printOverBluetooth(String deviceName, byte[] data) {
        BluetoothManager bm = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        BluetoothAdapter adapter = bm == null ? null : bm.getAdapter();
        if (adapter == null) {
            deliverPrintResult(false, "На планшеті немає Bluetooth");
            return;
        }
        if (!adapter.isEnabled()) {
            deliverPrintResult(false, "Увімкніть Bluetooth на планшеті");
            return;
        }
        BluetoothDevice device = findPrinter(adapter.getBondedDevices(), deviceName);
        if (device == null) {
            deliverPrintResult(false, "Спарений принтер не знайдено — спаруйте його в налаштуваннях Bluetooth");
            return;
        }
        BluetoothSocket sock = null;
        try {
            sock = device.createRfcommSocketToServiceRecord(SPP_UUID);
            sock.connect();
            OutputStream out = sock.getOutputStream();
            out.write(data);
            out.flush();
            Thread.sleep(300);
            deliverPrintResult(true, "Чек надруковано");
        } catch (Exception e) {
            deliverPrintResult(false, "Принтер Bluetooth недоступний (" + device.getName() + ")");
        } finally {
            if (sock != null) {
                try {
                    sock.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private static BluetoothDevice findPrinter(Set<BluetoothDevice> bonded, String wanted) {
        if (bonded == null || bonded.isEmpty()) {
            return null;
        }
        String want = wanted == null ? "" : wanted.trim().toLowerCase(Locale.ROOT);
        BluetoothDevice guess = null;
        for (BluetoothDevice d : bonded) {
            String name = d.getName() == null ? "" : d.getName().toLowerCase(Locale.ROOT);
            if (!want.isEmpty()) {
                if (name.equals(want)) {
                    return d;
                }
                continue;
            }
            BluetoothClass c = d.getBluetoothClass();
            boolean imaging = c != null && c.getMajorDeviceClass() == BluetoothClass.Device.Major.IMAGING;
            if (guess == null && (imaging || name.contains("print") || name.contains("pos") || name.contains("xp-"))) {
                guess = d;
            }
        }
        if (!want.isEmpty()) {
            return null;
        }
        return guess != null ? guess : (bonded.size() == 1 ? bonded.iterator().next() : null);
    }

    private void deliverPrintResult(boolean ok, String message) {
        runOnUiThread(() -> {
            if (web == null) {
                return;
            }
            web.evaluateJavascript("window.mdsPrintResult && window.mdsPrintResult(" + ok + ","
                    + JSONObject.quote(message == null ? "" : message) + ")", null);
        });
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
