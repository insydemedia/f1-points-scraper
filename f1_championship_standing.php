<?php
/**
 * F1 Championship Standings Proxy
 *
 * Fetches the latest standings JSON from GitHub (updated daily by GitHub Actions)
 * and returns it with the correct Content-Type header.
 *
 * Upload this file ONCE to your web server. No further changes needed.
 * Update GITHUB_JSON_URL below if you ever rename the repo or move branches.
 */

define('GITHUB_JSON_URL', 'https://raw.githubusercontent.com/YOUR_ORG/YOUR_REPO/main/f1_championship_standing.json');

header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');   // Allow cross-origin requests from your website

$json = @file_get_contents(GITHUB_JSON_URL);

if ($json === false) {
    http_response_code(502);
    echo json_encode(['error' => 'Failed to fetch standings from source.']);
    exit;
}

echo $json;
