<?php
// DEV-only HTTPS proxy.  It deliberately forwards the session cookie and
// backend Set-Cookie header; the shared production proxy remains untouched.
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(200); exit; }
$endpoint = $_GET['endpoint'] ?? '';
if (!preg_match('/^[a-z_]+$/', $endpoint)) { http_response_code(400); echo json_encode(['error' => 'Invalid endpoint']); exit; }
$params = $_GET; unset($params['endpoint'], $params['env']);
$url = 'http://127.0.0.1:8766/api/' . $endpoint . (empty($params) ? '' : '?' . http_build_query($params));
$ch = curl_init($url);
curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 1900, CURLOPT_HEADER => true]);
$headers = ['Content-Type: application/json'];
if (!empty($_SERVER['HTTP_COOKIE'])) $headers[] = 'Cookie: ' . $_SERVER['HTTP_COOKIE'];
if ($_SERVER['REQUEST_METHOD'] === 'POST') { $body = file_get_contents('php://input'); curl_setopt($ch, CURLOPT_POST, true); curl_setopt($ch, CURLOPT_POSTFIELDS, $body); $headers[] = 'Content-Length: ' . strlen($body); }
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
$raw = curl_exec($ch); $status = curl_getinfo($ch, CURLINFO_HTTP_CODE); $headerSize = curl_getinfo($ch, CURLINFO_HEADER_SIZE); $error = curl_error($ch); curl_close($ch);
if ($raw === false || $error) { http_response_code(502); echo json_encode(['error' => 'DEV API proxy unavailable']); exit; }
$backendHeaders = substr($raw, 0, $headerSize); $response = substr($raw, $headerSize);
foreach (explode("\r\n", $backendHeaders) as $line) if (stripos($line, 'Set-Cookie:') === 0) header($line, false);
http_response_code($status); echo $response;
?>
