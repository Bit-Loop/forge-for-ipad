use forge_accelerator::Client;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let token = std::env::var("FORGE_ACCEL_TOKEN")?;
    let capabilities = Client::new(token)?.capabilities().await?;
    println!(
        "boot={} CoreML={} Metal={}",
        capabilities.boot_id, capabilities.coreml.available, capabilities.metal.available
    );
    Ok(())
}
