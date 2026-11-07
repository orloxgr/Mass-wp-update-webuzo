<?php
// Authentication failures must stop before any native privileged request.
define('SOFTACULOUS', true);
function register_activation_hook(...$args) {}
function register_deactivation_hook(...$args) {}
function add_filter(...$args) {}
function admin_act(...$args) {}
require dirname(__DIR__) . '/mass-wp-update-webuzo.php';
function rejects(callable $callback, string $message): void {
    try { $callback(); }
    catch (Throwable $error) {
        if (strpos($error->getMessage(), $message) !== false) return;
        throw $error;
    }
    throw new RuntimeException('Expected request rejection: ' . $message);
}
$_POST = ['payload'=>json_encode(['action'=>'scan'])];
rejects(static fn() => mwu_api('root', 'valid-token', false), 'Session expired');
$_POST['token'] = ['valid-token'];
rejects(static fn() => mwu_api('root', 'valid-token', false), 'Session expired');
$_POST['token'] = 'wrong-token';
rejects(static fn() => mwu_api('root', 'valid-token', false), 'Session expired');
$_POST['token'] = 'valid-token';
rejects(static fn() => mwu_api('root', 'valid-token', true), 'demo mode');
$_POST['payload'] = json_encode(['action'=>'scan','actor'=>'root']);
rejects(static fn() => mwu_api('root', 'valid-token', false), 'Invalid request');
$_POST['payload'] = json_encode(['action'=>'upload_chunk']);
$_FILES = [];
rejects(static fn() => mwu_api('root', 'valid-token', false), 'ZIP chunk');
$_POST = ['mwu_download'=>'1','token'=>'valid-token','payload'=>json_encode(['action'=>'scan'])];
rejects(static fn() => mwu_api('root', 'valid-token', false), 'Invalid download');
$_SERVER['REQUEST_METHOD'] = 'POST'; $_SERVER['HTTP_X_MWU_REQUEST'] = '1';
$SESS = ['user'=>'customer','token'=>'valid-token'];
ob_start(); mwu_theme(); $result = json_decode(ob_get_clean(), true);
if (http_response_code() !== 403 || $result['ok'] !== false) throw new RuntimeException('Customer reached administrator actions.');
echo "Eight native-panel request rejection checks passed.\n";
