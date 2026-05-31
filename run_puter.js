const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const fs = require('fs');
const path = require('path');

// Add stealth plugin so Puter doesn't block the headless browser
puppeteer.use(StealthPlugin());

const STOCKS = ["Lumax Auto Technologies", "BEL"]; // Your target stocks

async function runPuterAutomation() {
    console.log("🚀 Launching Stealth Browser...");
    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox'] // Required for GitHub Actions
    });

    const page = await browser.newPage();
    
    // Get the absolute path to our dummy HTML file
    const htmlPath = `file://${path.join(__dirname, 'puter.html')}`;
    await page.goto(htmlPath);

    console.log("⏳ Waiting for Puter.js to initialize...");
    // Wait until the puter object is fully loaded into the window
    await page.waitForFunction(() => window.puter !== undefined);
    console.log("✅ Puter.js Loaded!");

    for (const stock of STOCKS) {
        console.log(`\n🤖 Processing: ${stock} via Puter (Claude 3.5 Sonnet)...`);
        
        // This is where you would inject your massive Master Prompt + Stock Name
        const prompt = `Analyze the stock ${stock} and output a JSON payload summarizing its key financial metrics. Do not include markdown formatting.`;

        try {
            // Execute code INSIDE the hidden browser tab
            const aiResponse = await page.evaluate(async (textPrompt) => {
                // CHANGED: We now request Claude 3.5 Sonnet from Puter's router
                const response = await puter.ai.chat(textPrompt, { model: 'claude-3-5-sonnet' });
                return response.message;
            }, prompt);

            console.log(`✅ Success for ${stock}! Response preview:`, aiResponse.substring(0, 100) + "...");
            
            // Save the result to a file
            fs.writeFileSync(`${stock.replace(/ /g, '_')}_report.json`, aiResponse);
            
        } catch (error) {
            console.error(`❌ Failed processing ${stock}:`, error.message);
        }
        
        // Wait 15 seconds between requests so Puter doesn't IP ban us for spamming
        console.log("⏸️ Cooling down for 15 seconds...");
        await new Promise(r => setTimeout(r, 15000));
    }

    console.log("\n🏁 All tasks complete. Closing browser.");
    await browser.close();
}

runPuterAutomation();
