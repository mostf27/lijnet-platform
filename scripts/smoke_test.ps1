param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$passed = 0
$failed = 0

function Assert-Test {
    param([string]$Name, [bool]$Condition, [string]$Detail = "")
    if ($Condition) {
        Write-Host "[PASS] $Name" -ForegroundColor Green
        $script:passed++
    } else {
        Write-Host "[FAIL] $Name - $Detail" -ForegroundColor Red
        $script:failed++
    }
}

Write-Host "`n=== Lijnet End-to-End Smoke Test ($BaseUrl) ===" -ForegroundColor Cyan
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

try {
    # 1. Health Check
    $health = Invoke-RestMethod -Uri "$BaseUrl/healthz" -Method Get
    Assert-Test "GET /healthz returns ok" ($health.status -eq "ok") "Got: $($health | ConvertTo-Json -Compress)"

    # 2. English Storefront
    $homeEn = Invoke-WebRequest -Uri "$BaseUrl/" -WebSession $session -UseBasicParsing
    Assert-Test "GET / (EN storefront)" ($homeEn.StatusCode -eq 200 -and $homeEn.Content -match 'lang="en"') "Status: $($homeEn.StatusCode)"

    # 3. Bilingual Toggle -> Amharic
    $homeAm = Invoke-WebRequest -Uri "$BaseUrl/set-language/am" -WebSession $session -UseBasicParsing
    Assert-Test "GET /set-language/am switches HTML to Amharic" ($homeAm.StatusCode -eq 200 -and $homeAm.Content -match 'lang="am"') "Expected lang=am"

    $null = Invoke-WebRequest -Uri "$BaseUrl/set-language/en" -WebSession $session -UseBasicParsing

    # 4. Product Detail & Proclamation 1205/2020 Disclosure
    $prod = Invoke-WebRequest -Uri "$BaseUrl/products/2" -WebSession $session -UseBasicParsing
    Assert-Test "GET /products/2 renders 7-day return disclosure" ($prod.StatusCode -eq 200 -and $prod.Content -match "1205/2020") "Missing disclosure"

    # 5. HTMX Add to Cart
    $cartAdd = Invoke-WebRequest -Uri "$BaseUrl/cart/add" -Method Post `
        -Headers @{ "HX-Request" = "true" } `
        -Body @{ product_id = "2"; quantity = "1" } `
        -WebSession $session -UseBasicParsing
    Assert-Test "POST /cart/add (HTMX) returns #cart-badge" ($cartAdd.StatusCode -eq 200 -and $cartAdd.Content -match 'id="cart-badge"') "Body: $($cartAdd.Content)"

    # 6. HTMX Sub-City Delivery Fee Estimation (Yeka = 200.00 ETB)
    $est = Invoke-WebRequest -Uri "$BaseUrl/cart/estimate?sub_city=Yeka" -WebSession $session -UseBasicParsing
    Assert-Test "GET /cart/estimate?sub_city=Yeka calculates 200.00 ETB" ($est.StatusCode -eq 200 -and $est.Content -match "200\.00") "Body: $($est.Content)"

    # 7. Checkout Page
    $chkPage = Invoke-WebRequest -Uri "$BaseUrl/checkout" -WebSession $session -UseBasicParsing
    Assert-Test "GET /checkout renders cart and Addis sub-cities" ($chkPage.StatusCode -eq 200 -and $chkPage.Content -match "Bole") "Status: $($chkPage.StatusCode)"

    # 8. Submit Addis Ababa Checkout (Chapa Sandbox)
    $chkSubmit = Invoke-WebRequest -Uri "$BaseUrl/checkout/submit" -Method Post `
        -Body @{
            recipient_name       = "Abebe Bikila"
            recipient_phone      = "+251911998877"
            customer_email       = "smoke.test@lijnet.et"
            sub_city             = "Bole"
            woreda               = "03"
            landmark_description = "Near Bole Atlas traffic light, yellow gate"
            gps_coordinates      = "8.9990,38.7890"
            payment_method       = "CHAPA"
        } -WebSession $session -UseBasicParsing
    Assert-Test "POST /checkout/submit completes Chapa sandbox & marks PAID" ($chkSubmit.StatusCode -eq 200 -and $chkSubmit.Content -match "Abebe Bikila" -and $chkSubmit.Content -match "PAID") "Confirmation missing"

    # 9. Orders & 7-Day Return Page
    $ordersPage = Invoke-WebRequest -Uri "$BaseUrl/orders" -WebSession $session -UseBasicParsing
    Assert-Test "GET /orders shows seeded order & 7-day return form" ($ordersPage.StatusCode -eq 200 -and $ordersPage.Content -match "LIJ-2026-1001") "Seeded order missing"

    # 10. Admin Dashboard & 50% Local Compliance
    $adminPage = Invoke-WebRequest -Uri "$BaseUrl/admin" -WebSession $session -UseBasicParsing
    Assert-Test "GET /admin verifies >=50% Local Ethiopian compliance" ($adminPage.StatusCode -eq 200 -and $adminPage.Content -match "COMPLIANT") "Compliance badge missing"

    # 11. Rider Dispatch CSV Export
    $csvResp = Invoke-WebRequest -Uri "$BaseUrl/admin/orders/export-csv" -WebSession $session -UseBasicParsing
    $isCsv = ($csvResp.Headers["Content-Type"] -match "text/csv") -and ($csvResp.Content -match "Order Number,Status,Sub-City")
    Assert-Test "GET /admin/orders/export-csv returns Addis rider CSV" $isCsv "Content-Type: $($csvResp.Headers['Content-Type'])"
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Make sure uvicorn is running in Tab 1 on $BaseUrl." -ForegroundColor Yellow
    $failed++
}

Write-Host "`n=== Results: $passed Passed | $failed Failed ===" -ForegroundColor Cyan
