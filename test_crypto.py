#!/usr/bin/env python3
"""
Test script for the Crypto Arbitrage Module.

Tests:
1. Binance WebSocket connection and price feeds
2. Crypto market discovery on Polymarket
3. Arbitrage detection logic
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import get_config
from src.logger import get_logger

logger = get_logger("test_crypto")


async def test_binance_connection():
    """Test Binance WebSocket connection and price feeds."""
    print("\n" + "="*60)
    print("TEST 1: Binance WebSocket Connection")
    print("="*60)
    
    try:
        from src.crypto.binance_provider import BinanceProvider
        
        config = get_config()
        
        # Check if Binance is configured
        if not config.crypto.binance_api_key:
            print("⚠️  BINANCE_API_KEY not set in .env")
            print("   Binance provider will still work for public data (prices)")
        
        # Initialize provider
        symbols = config.crypto.crypto_pairs.split(",")
        print(f"📊 Initializing Binance provider for: {symbols}")
        
        binance = BinanceProvider(
            api_key=config.crypto.binance_api_key,
            api_secret=config.crypto.binance_api_secret,
            symbols=symbols
        )
        
        # Connect (fetches initial prices via REST)
        await binance.connect()
        print("✅ Binance connected!")
        
        # Check initial prices
        prices = binance.get_all_prices()
        if prices:
            print("\n📈 Current Prices (from REST API):")
            for symbol, price_data in prices.items():
                print(f"   {symbol}: ${price_data.price:,.2f}")
                print(f"      Bid: ${price_data.bid:,.2f} | Ask: ${price_data.ask:,.2f}")
                print(f"      Spread: {price_data.spread:.4f}%")
                print(f"      24h Volume: {price_data.volume_24h:,.0f}")
        else:
            print("⚠️  No prices fetched - check network connection")
        
        # Test threshold monitoring
        print("\n📍 Setting up threshold monitoring:")
        binance.add_threshold("BTCUSDT", 100000)  # $100K
        binance.add_threshold("BTCUSDT", 95000)   # $95K
        binance.add_threshold("ETHUSDT", 4000)    # $4K
        binance.add_threshold("SOLUSDT", 200)     # $200
        
        # Check distance to thresholds
        btc_price = binance.get_price("BTCUSDT")
        if btc_price:
            dist_100k = binance.get_distance_to_threshold("BTCUSDT", 100000)
            dist_95k = binance.get_distance_to_threshold("BTCUSDT", 95000)
            print(f"\n   BTC Distance to $100K: {dist_100k:+.2f}%")
            print(f"   BTC Distance to $95K: {dist_95k:+.2f}%")
            
            if binance.is_approaching_threshold("BTCUSDT", 100000, within_pct=5):
                print("   🚀 BTC is within 5% of $100K!")
        
        # Test WebSocket for 5 seconds
        print("\n🔌 Testing WebSocket stream (5 seconds)...")
        
        update_count = [0]
        
        async def on_price_update(symbol, price_data):
            update_count[0] += 1
            if update_count[0] <= 3:  # Only print first 3
                print(f"   📊 {symbol}: ${price_data.price:,.2f}")
        
        binance.on_price_update(on_price_update)
        
        # Start WebSocket in background
        ws_task = asyncio.create_task(binance.start_websocket_stream())
        
        # Wait 5 seconds
        await asyncio.sleep(5)
        
        # Cancel WebSocket
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        
        print(f"\n✅ Received {update_count[0]} price updates in 5 seconds")
        print(f"   Average latency: {binance.avg_latency_ms:.1f}ms")
        
        await binance.disconnect()
        print("✅ Binance test PASSED!")
        return True
        
    except Exception as e:
        print(f"❌ Binance test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_crypto_arbitrage_detector():
    """Test crypto arbitrage detection logic."""
    print("\n" + "="*60)
    print("TEST 2: Crypto Arbitrage Detector")
    print("="*60)
    
    try:
        from src.crypto.binance_provider import BinanceProvider
        from src.crypto.crypto_arbitrage import CryptoArbitrageDetector, CryptoMarket
        
        config = get_config()
        
        # Initialize Binance
        symbols = ["BTCUSDT", "ETHUSDT"]
        binance = BinanceProvider(symbols=symbols)
        await binance.connect()
        
        # Initialize arbitrage detector
        detector = CryptoArbitrageDetector(
            binance=binance,
            min_edge=0.05  # 5% minimum edge
        )
        print("✅ Arbitrage detector initialized")
        
        # Create mock crypto markets
        btc_price = binance.get_price("BTCUSDT")
        if btc_price:
            # Create a market slightly above current price
            threshold = round(btc_price.price * 1.02 / 1000) * 1000  # Round to nearest $1000
            
            mock_market = CryptoMarket(
                market_id="test-btc-market",
                condition_id="test-btc-condition",
                token_id_yes="12345678901234567890",
                token_id_no="09876543210987654321",
                question=f"Will Bitcoin hit ${threshold:,.0f}?",
                symbol="BTCUSDT",
                threshold=threshold,
                direction="above",
                deadline=datetime.utcnow() + timedelta(days=7),
                current_yes_price=0.35,  # Market thinks 35% chance
                current_no_price=0.65
            )
            
            detector.add_market(mock_market)
            print(f"\n📊 Added mock market: {mock_market.question}")
            print(f"   Current BTC: ${btc_price.price:,.2f}")
            print(f"   Threshold: ${threshold:,.0f}")
            print(f"   Distance: {(threshold - btc_price.price) / btc_price.price * 100:.2f}%")
            print(f"   Market Yes Price: {mock_market.current_yes_price*100:.0f}%")
            
            # Check for opportunities
            print("\n🔍 Checking for opportunities...")
            opportunities = await detector.check_opportunities()
            
            if opportunities:
                for opp in opportunities:
                    print(f"\n🎯 OPPORTUNITY FOUND!")
                    print(f"   Market: {opp.market.question}")
                    print(f"   Current Price: ${opp.current_price:,.2f}")
                    print(f"   Distance to threshold: {opp.distance_to_threshold_pct:+.2f}%")
                    print(f"   Our probability: {opp.model_probability*100:.1f}%")
                    print(f"   Market probability: {opp.market_probability*100:.1f}%")
                    print(f"   EDGE: {opp.edge*100:.1f}%")
                    print(f"   Direction: {opp.direction}")
                    print(f"   Confidence: {opp.confidence*100:.0f}%")
            else:
                print("   No opportunities found (edge < 5%)")
                
                # Show what the calculation looks like anyway
                summary = detector.get_market_summary()
                for market_id, info in summary.items():
                    print(f"\n   Market: {info['question']}")
                    print(f"   Current price: ${info['current_price']:,.2f}")
                    print(f"   Distance: {info['distance_pct']:.2f}%")
        
        await binance.disconnect()
        print("\n✅ Arbitrage detector test PASSED!")
        return True
        
    except Exception as e:
        print(f"❌ Arbitrage detector test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_polymarket_crypto_search():
    """Test searching for crypto markets on Polymarket."""
    print("\n" + "="*60)
    print("TEST 3: Polymarket Crypto Market Search")
    print("="*60)
    
    try:
        from src.trading.polymarket_client import PolymarketClient
        
        client = PolymarketClient()
        await client.connect()
        print("✅ Connected to Polymarket")
        
        # Search for crypto markets
        keywords = ["bitcoin", "btc", "ethereum", "crypto"]
        
        all_markets = []
        for keyword in keywords:
            print(f"\n🔍 Searching for '{keyword}' markets...")
            markets = await client.search_markets(keyword)
            print(f"   Found {len(markets)} markets")
            
            for market in markets[:3]:  # Show first 3
                print(f"\n   📊 {market.question[:60]}...")
                print(f"      Yes: {market.yes_price*100:.1f}% | No: {market.no_price*100:.1f}%")
                if market.end_date:
                    print(f"      Ends: {market.end_date}")
            
            all_markets.extend(markets)
        
        # Deduplicate
        unique_markets = {m.market_id: m for m in all_markets}
        print(f"\n📊 Total unique crypto markets found: {len(unique_markets)}")
        
        await client.disconnect()
        print("✅ Polymarket search test PASSED!")
        return True
        
    except Exception as e:
        print(f"❌ Polymarket search test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "🚀"*30)
    print("   CRYPTO ARBITRAGE MODULE TEST SUITE")
    print("🚀"*30)
    
    results = []
    
    # Test 1: Binance connection
    results.append(("Binance Connection", await test_binance_connection()))
    
    # Test 2: Arbitrage detector
    results.append(("Arbitrage Detector", await test_crypto_arbitrage_detector()))
    
    # Test 3: Polymarket search
    results.append(("Polymarket Search", await test_polymarket_crypto_search()))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"   {name}: {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "="*60)
    if all_passed:
        print("🎉 ALL TESTS PASSED! Crypto module is ready.")
    else:
        print("⚠️  Some tests failed. Check the output above.")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
