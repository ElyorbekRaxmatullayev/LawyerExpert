<?php

declare(strict_types=1);

const BOT_TOKEN   = '8984770867:AAEsncu5pSp9gWpwLwb-grGGtfskIXVgKhw';
const CHAT_IDS    = ['1913880636', '5884034743', '2077634702'];
const SITE_URL    = 'https://legalexpert.uz';
const RATE_LIMIT  = 3;
const RATE_WINDOW = 600;

header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');

function reply(bool $ok, string $error = '', int $code = 200): never {
    http_response_code($code);
    echo json_encode($ok ? ['ok' => true] : ['ok' => false, 'error' => $error],
                     JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    reply(false, 'Метод не поддерживается', 405);
}

$raw  = file_get_contents('php://input');
$data = json_decode($raw ?: '', true);
if (!is_array($data)) { $data = $_POST; }

function field(array $d, string $key, int $max): string {
    $v = trim(strip_tags((string)($d[$key] ?? '')));
    return mb_substr($v, 0, $max);
}

if (field($data, 'hp', 100) !== '') { reply(true); }

$name = field($data, 'name', 100);
$tel  = field($data, 'tel', 100);
$type = field($data, 'type', 80);
$msg  = field($data, 'msg', 2000);

if ($name === '' || $tel === '') { reply(false, 'Заполните имя и контакт', 400); }

$ip   = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? '-';
$ip   = trim(explode(',', $ip)[0]);
$file = sys_get_temp_dir() . '/lead_' . md5($ip) . '.txt';
$now  = time();
$hits = array_values(array_filter(
    is_file($file) ? array_map('intval', explode(',', (string)file_get_contents($file))) : [],
    fn($t) => $now - $t < RATE_WINDOW
));
if (count($hits) >= RATE_LIMIT) { reply(false, 'Заявка уже отправлена', 429); }
$hits[] = $now;
@file_put_contents($file, implode(',', $hits));

$e = fn(string $s): string => htmlspecialchars($s, ENT_NOQUOTES, 'UTF-8');

$text = "🔔 <b>Новая заявка с сайта</b>\n\n"
      . "👤 <b>Имя:</b> "      . $e($name) . "\n"
      . "📞 <b>Контакт:</b> "  . $e($tel)  . "\n"
      . "📄 <b>Документ:</b> " . $e($type !== '' ? $type : '—') . "\n\n"
      . "📝 <b>Задача:</b>\n"  . $e($msg  !== '' ? $msg  : '—') . "\n\n"
      . "🕒 " . date('d.m.Y H:i') . "  ·  " . SITE_URL;

$delivered = 0;
foreach (CHAT_IDS as $chatId) {
    $ch = curl_init('https://api.telegram.org/bot' . BOT_TOKEN . '/sendMessage');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 8,
        CURLOPT_POST           => true,
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS     => json_encode([
            'chat_id'                  => $chatId,
            'text'                     => $text,
            'parse_mode'               => 'HTML',
            'disable_web_page_preview' => true,
        ], JSON_UNESCAPED_UNICODE),
    ]);
    $res  = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($code === 200 && ($json = json_decode((string)$res, true)) && ($json['ok'] ?? false)) {
        $delivered++;
    }
}

@file_put_contents(
    __DIR__ . '/leads.log',
    json_encode(['ts' => date('c'), 'delivered' => $delivered, 'name' => $name,
                 'tel' => $tel, 'type' => $type, 'msg' => $msg], JSON_UNESCAPED_UNICODE) . "\n",
    FILE_APPEND
);

$delivered > 0 ? reply(true) : reply(false, 'Сервис уведомлений недоступен', 502);
