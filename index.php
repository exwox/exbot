<?php
// --- PENTING: GANTI DENGAN KEY ANDA ---
$apiKey = 'YOUR_API_KEY';
$secretKey = 'YOUR_SECRET_KEY';
// ----------------------------------------

// URL API Indodax untuk Private API
$api_url = 'https://indodax.com/tapi';

// Method yang digunakan untuk cek saldo
$method = 'getInfo';

// Nonce harus berupa integer yang selalu meningkat (lebih besar dari permintaan sebelumnya)
// Disarankan menggunakan timestamp UNIX saat ini untuk nonce
$nonce = time();

// Data POST yang akan dikirim
$post_data = array(
    'method' => $method,
    'nonce'  => $nonce
);

// Membuat query string dari data POST
$post_query = http_build_query($post_data, '', '&');

// Membuat signature HMAC-SHA512
// Signature dihitung dari string data POST menggunakan Secret Key
$signature = hash_hmac('sha512', $post_query, $secretKey);

// Header HTTP yang diperlukan
$headers = array(
    'Key: ' . $apiKey,
    'Sign: ' . $signature
);

// Inisialisasi cURL
$ch = curl_init();

// Set opsi cURL
curl_setopt($ch, CURLOPT_URL, $api_url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true); // Mengembalikan hasil transfer sebagai string
curl_setopt($ch, CURLOPT_POST, true);           // Set permintaan sebagai POST
curl_setopt($ch, CURLOPT_POSTFIELDS, $post_query); // Data POST
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers); // Header HTTP

// Eksekusi cURL
$response = curl_exec($ch);

// Tutup cURL
curl_close($ch);

// Dekode respon JSON
$result = json_decode($response, true);

// Tampilkan hasil
echo "<h2>Hasil Cek Saldo Indodax</h2>";

if ($result['success'] == 1) {
    echo "<h3>Saldo Tersedia (Balance)</h3>";
    echo "<ul>";
    // Looping untuk menampilkan setiap saldo
    foreach ($result['return']['balance'] as $currency => $amount) {
        // Hanya tampilkan jika saldo > 0
        if ($amount > 0) {
            echo "<li>" . strtoupper($currency) . ": **" . number_format($amount, 8) . "**</li>";
        }
    }
    echo "</ul>";

    echo "<h3>Saldo Ditahan (Hold) - Untuk order yang belum terisi</h3>";
    echo "<ul>";
    // Looping untuk menampilkan setiap hold
    foreach ($result['return']['balance_hold'] as $currency => $amount) {
         // Hanya tampilkan jika saldo ditahan > 0
        if ($amount > 0) {
            echo "<li>" . strtoupper($currency) . " Hold: **" . number_format($amount, 8) . "**</li>";
        }
    }
    echo "</ul>";

    // Contoh menampilkan data spesifik
    echo "<p>Saldo Rupiah (IDR): **" . number_format($result['return']['balance']['idr'], 2) . "**</p>";

} else {
    // Tampilkan pesan error jika gagal
    echo "<p style='color: red;'>**Error:** " . htmlspecialchars($result['error']) . "</p>";
}

// Tampilkan respon mentah untuk debugging
// echo "<pre>";
// print_r($result);
// echo "</pre>";
?>