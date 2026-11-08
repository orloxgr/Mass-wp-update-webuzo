<?php
/*
Plugin Name: Mass WP Update for Webuzo
Version: 0.4.2
Description: Update WordPress sites and manage a private premium plugin library.
Author: Byron Iniotakis
Author URI: https://www.iniotakis.com
License: GPL-3.0-or-later
*/
if (!defined('SOFTACULOUS')) { http_response_code(403); exit; }

register_activation_hook(__FILE__, 'mwu_activate');
register_deactivation_hook(__FILE__, 'mwu_deactivate');
add_filter('admin_menu', 'mwu_menu', 10, 1);
admin_act('mass-wp-update-webuzo', 'mwu_page');

function mwu_lifecycle(string $operation): bool {
    global $W, $error;
    if (!in_array($operation, ['install', 'remove'], true)) return false;
    $result = $W->wexec('/usr/bin/python3 -I -B ' . escapeshellarg(__DIR__ . '/src/lifecycle.py') . ' ' . $operation);
    $data = json_decode(implode("\n", $result['output'] ?? []), true);
    if (($result['retval'] ?? 1) !== 0 || !is_array($data) || empty($data['ok'])) {
        $error[] = is_array($data) ? ($data['error'] ?? 'Plugin activation failed.') : 'Plugin returned an invalid response.';
        return false;
    }
    return true;
}
function mwu_activate() { return mwu_lifecycle('install'); }
function mwu_deactivate() { return mwu_lifecycle('remove'); }
function mwu_menu($menu) {
    $menu['mass-wp-update-webuzo'] = ['name'=>'Mass WP Update', 'href'=>'index.php?act=mass-wp-update-webuzo', 'icon'=>'fas fa-sync-alt'];
    return $menu;
}
function mwu_page() {
    if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
        mwu_theme(); exit;
    }
    theme_for_act('mwu_theme', 'mass-wp-update-webuzo');
}
function mwu_bridge(array $request): array {
    global $W;
    $identity = bin2hex(random_bytes(32));
    $path = '/run/mass-wp-update-webuzo/' . $identity . '.json';
    $payload = json_encode($request, JSON_THROW_ON_ERROR);
    if (strlen($payload) > 1024 * 1024) throw new RuntimeException('Request exceeds 1 MiB.');
    $mask = umask(0077);
    try { $handle = fopen($path, 'x'); }
    finally { umask($mask); }
    if ($handle === false) throw new RuntimeException('Activate Mass WP Update before using it.');
    try {
        $offset = 0;
        while ($offset < strlen($payload)) {
            $written = fwrite($handle, substr($payload, $offset));
            if ($written === false || $written === 0) throw new RuntimeException('Could not send the request.');
            $offset += $written;
        }
        fclose($handle); $handle = null;
        $result = $W->wexec('/usr/bin/python3 -I -B ' . escapeshellarg(__DIR__ . '/src/addon.py') . ' --request ' . $identity);
        $data = json_decode(implode("\n", $result['output'] ?? []), true);
        if (!is_array($data)) throw new RuntimeException('The worker returned an invalid response.');
        if (($data['response'] ?? '') === $identity) {
            $responsePath = '/run/mass-wp-update-webuzo/' . $identity . '.response';
            $info = lstat($responsePath);
            if ($info === false || ($info['mode'] & 0170000) !== 0100000 || ($info['mode'] & 0777) !== 0600 || $info['nlink'] !== 1) throw new RuntimeException('Invalid private response file.');
            $data = json_decode(file_get_contents($responsePath), true, 512, JSON_THROW_ON_ERROR);
            if (!is_array($data)) throw new RuntimeException('Invalid worker response.');
        }
        return $data;
    } finally {
        if (is_resource($handle)) fclose($handle);
        if (is_file($path)) unlink($path);
        $responsePath = '/run/mass-wp-update-webuzo/' . $identity . '.response';
        if (is_file($responsePath)) unlink($responsePath);
    }
}
function mwu_api(string $actor, string $csrf, bool $demo): array {
    if ($demo) throw new RuntimeException('Changes are unavailable in demo mode.');
    $token = $_POST['token'] ?? null;
    if (!is_string($token) || !hash_equals($csrf, $token)) throw new RuntimeException('Session expired. Reload this page and try again.');
    $payload = $_POST['payload'] ?? '';
    if (!is_string($payload) || strlen($payload) > 1024 * 1024) throw new RuntimeException('Invalid request size.');
    $request = json_decode($payload, true, 32, JSON_THROW_ON_ERROR);
    if (!is_array($request) || isset($request['actor']) || isset($request['admin'])) throw new RuntimeException('Invalid request.');
    if ((($request['action'] ?? '') === 'download_prepare') !== (($_POST['mwu_download'] ?? '') === '1')) throw new RuntimeException('Invalid download request.');
    $part = null;
    try {
        if (($request['action'] ?? '') === 'upload_chunk') {
            $upload = $_FILES['chunk'] ?? null;
            if (!is_array($upload) || ($upload['error'] ?? -1) !== UPLOAD_ERR_OK ||
                !is_int($upload['size']) || $upload['size'] < 1 || $upload['size'] > 1024 * 1024 ||
                !is_uploaded_file($upload['tmp_name'])) throw new RuntimeException('Could not receive the ZIP chunk.');
            $identity = bin2hex(random_bytes(32));
            $part = '/run/mass-wp-update-webuzo/' . $identity . '.part';
            $mask = umask(0077);
            try { $handle = fopen($part, 'x'); }
            finally { umask($mask); }
            if ($handle === false) throw new RuntimeException('Could not stage the ZIP chunk.');
            try {
                $source = fopen($upload['tmp_name'], 'rb');
                if ($source === false) throw new RuntimeException('Could not read the ZIP chunk.');
                try {
                    if (stream_copy_to_stream($source, $handle, 1024 * 1024 + 1) !== $upload['size']) throw new RuntimeException('ZIP chunk size mismatch.');
                } finally { fclose($source); }
            } finally { fclose($handle); }
            $request['part'] = $identity;
        } elseif (!empty($_FILES)) throw new RuntimeException('Unexpected upload.');
        $request['actor'] = $actor;
        $request['admin'] = true;
        return mwu_bridge($request);
    } finally {
        if ($part !== null && is_file($part)) unlink($part);
    }
}
function mwu_send_download(array $result): void {
    if (empty($result['ok'])) throw new RuntimeException($result['error'] ?? 'Could not prepare backup.');
    $download = $result['download'] ?? [];
    $token = $download['token'] ?? '';
    $filename = $result['filename'] ?? '';
    if (!is_string($token) || !preg_match('/^[0-9a-f]{64}$/D', $token) || !is_string($filename) || !preg_match('/^[0-9a-f]{32}-(plugins\.tar\.gz|themes\.tar\.gz|database\.sql|(?:plugins|themes|database)\.zip|manifest\.json)$/D', $filename)) throw new RuntimeException('Invalid backup response.');
    $path = '/var/lib/mass-wp-update-webuzo-downloads/' . $token . '.download';
    $handle = null;
    try {
        $info = lstat($path);
        if ($info === false || ($info['mode'] & 0170000) !== 0100000 || ($info['mode'] & 0777) !== 0600 || $info['nlink'] !== 1 || $info['uid'] !== $download['uid'] || $info['size'] !== $download['bytes']) throw new RuntimeException('Invalid backup staging file.');
        $handle = fopen($path, 'rb');
        if ($handle === false) throw new RuntimeException('Could not open backup.');
        if (fstat($handle)['ino'] !== $info['ino']) throw new RuntimeException('Backup staging file changed.');
        while (ob_get_level()) { if (!ob_end_clean()) break; }
        header('Content-Type: application/octet-stream');
        header('Content-Disposition: attachment; filename="' . $filename . '"');
        header('Content-Length: ' . $info['size']);
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        while (!feof($handle)) {
            $block = fread($handle, 1024 * 1024);
            if ($block === false) throw new RuntimeException('Could not read backup.');
            echo $block; flush();
            if (connection_aborted()) break;
        }
    } finally {
        if (is_resource($handle)) fclose($handle);
        if (is_file($path)) unlink($path);
    }
}
function mwu_theme() {
    global $SESS, $user, $globals;
    $actor = $SESS['user'] ?? '';
    $csrf = $SESS['token'] ?? '';
    $ajax = ($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' && ($_SERVER['HTTP_X_MWU_REQUEST'] ?? '') === '1';
    if ($actor !== 'root' || !empty($SESS['is_reseller']) || !is_string($csrf) || $csrf === '') {
        http_response_code(403);
        if ($ajax) { header('Content-Type: application/json'); echo json_encode(['ok'=>false,'error'=>'Webuzo administrator access is required.']); }
        return;
    }
    $demo = !empty($user['demo']) || !empty($globals['demo']);
    if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' && ($_POST['mwu_download'] ?? '') === '1') {
        ignore_user_abort(true);
        try { mwu_send_download(mwu_api($actor, $csrf, $demo)); }
        catch (Throwable $exception) { http_response_code(400); header('Content-Type: text/plain; charset=UTF-8'); echo $exception->getMessage(); }
        return;
    }
    if ($ajax) {
        header('Content-Type: application/json; charset=UTF-8');
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        try { $result = mwu_api($actor, $csrf, $demo); }
        catch (Throwable $exception) { $result = ['ok'=>false,'error'=>$exception->getMessage()]; }
        echo json_encode($result, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_INVALID_UTF8_SUBSTITUTE);
        return;
    }
    $escape = static fn($value) => htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    require __DIR__ . '/panel/index.php';
}
