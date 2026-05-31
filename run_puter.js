const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const fs = require('fs');
const path = require('path');

puppeteer.use(StealthPlugin());

const STOCKS = ["Lumax Auto Technologies", "BEL"]; 

async function runPuterAutomation() {
    console.log("🚀 Launching Stealth Browser...");
    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox'] 
    });

    const page = await browser.newPage();
    
    // ==========================================================
    // THE X-RAY: Forward invisible browser logs to GitHub terminal
    // ==========================================================
    page.on('console', msg => console.log('🌐 [Browser Log]:', msg.text()));
    page.on('pageerror', err => console.error('❌ [Browser Error]:', err.toString()));
    page.on('requestfailed', request => {
        console.error(`⚠️ [Network Block]: ${request.url()} failed. Reason: ${request.failure().errorText}`);
    });

    const htmlPath = `file://${path.join(__dirname, 'puter.html')}`;
    await page.goto(htmlPath);

    console.log("⏳ Waiting for Puter.js to initialize...");
    await page.waitForFunction(() => window.puter !== undefined);
    console.log("✅ Puter.js Loaded!");

    for (const stock of STOCKS) {
        console.log(`\n🤖 Processing: ${stock} via Puter (Claude 3.5 Sonnet)...`);
        
        const prompt = `Analyze the stock ${stock} and output a JSON payload summarizing its key financial metrics. Do not include markdown formatting.`;

        try {
            // Added a 2-minute timeout so it doesn't hang forever
            const aiResponse = await page.evaluate(async (textPrompt) => {
                
                // Wrap in a Promise to enforce a timeout inside the browser
                const timeoutPromise = new Promise((_, reject) => 
                    setTimeout(() => reject(new Error("Puter API timed out after 120 seconds.")), 120000)
                );
                
                const puterPromise = puter.ai.chat(textPrompt, { model: 'claude-3-5-sonnet' });
                
                // Race the API call against the timeout
                const response = await Promise.race([puterPromise, timeoutPromise]);
                return response.message;
                
            }, prompt);

            console.log(`✅ Success for ${stock}! Preview:`, aiResponse.substring(0, 100) + "...");
            fs.writeFileSync(`${stock.replace(/ /g, '_')}_report.json`, aiResponse);
            
        } catch (error) {
            console.error(`❌ Failed processing ${stock}:`, error.message);
        }
        
        console.log("⏸️ Cooling down for 15 seconds...");
        await new Promise(r => setTimeout(r, 15000));
    }

    console.log("\n🏁 All tasks complete. Closing browser.");
    await browser.close();
}

runPuterAutomation();

