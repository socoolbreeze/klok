from rpi_ws281x import Adafruit_NeoPixel, Color
from time import sleep
from datetime import datetime, timezone
import signal
import sys
import subprocess
import os
import time as time_module
import threading
from collections import deque

# Configuratie
LED_COUNT = 64
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 5
LED_INVERT = False
LED_BRIGHTNESS = 50
REFRESH_INTERVAL = 1.0

# Time configuration
EXPECTED_TIMEZONE = "Europe/Amsterdam"
NTP_SYNC_REQUIRED = False  # Don't stop if NTP fails
NTP_CHECK_INTERVAL = 300   # Check NTP status every 5 minutes
MAX_DRIFT_MINUTES = 30     # Warn if clock drifts more than 30 minutes

class TimeManager:
    """Enhanced time manager with offline resilience"""
    
    def __init__(self):
        self.timezone_verified = False
        self.ntp_synced = False
        self.last_ntp_sync_time = None
        self.ntp_loss_time = None
        self.dst_transitions_logged = []
        self.last_dst_state = None
        self.startup_diagnostics_done = False
        self.time_drift_warnings = deque(maxlen=10)
        self.offline_mode = False
        
        # Background NTP monitoring
        self.ntp_monitor_thread = None
        self.stop_monitoring = False
    
    def verify_system_time(self):
        """Verify system time configuration"""
        print("\n" + "="*60)
        print("LED WORD CLOCK - TIME SYSTEM VERIFICATION")
        print("="*60)
        
        # Get current time info
        now = datetime.now()
        current_dst = time_module.localtime().tm_isdst
        
        print(f"Current Local Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"DST Currently Active: {'Yes' if current_dst else 'No'}")
        
        # Check timezone configuration
        self._check_timezone()
        
        # Check initial NTP synchronization
        self._check_ntp_sync()
        
        # Verify time source reliability
        self._verify_time_source()
        
        # Start background NTP monitoring
        self._start_ntp_monitoring()
        
        print("="*60)
        
        # Set initial DST state for monitoring
        self.last_dst_state = current_dst
        self.startup_diagnostics_done = True
        
        return self.timezone_verified  # Only require timezone, not NTP
    
    def _check_timezone(self):
        """Check if timezone is correctly configured"""
        try:
            result = subprocess.run(['timedatectl'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output = result.stdout
                
                if 'Time zone:' in output:
                    tz_line = [line for line in output.split('\n') if 'Time zone:' in line][0]
                    timezone_info = tz_line.split('Time zone:')[1].strip()
                    print(f"System Timezone: {timezone_info}")
                    
                    if EXPECTED_TIMEZONE in timezone_info:
                        print(f"✅ Timezone correctly set to {EXPECTED_TIMEZONE}")
                        self.timezone_verified = True
                    else:
                        print(f"⚠️  Timezone mismatch!")
                        print(f"   Expected: {EXPECTED_TIMEZONE}")
                        print(f"   Current: {timezone_info}")
                        print(f"   Fix with: sudo timedatectl set-timezone {EXPECTED_TIMEZONE}")
                        self.timezone_verified = False
                else:
                    print("❌ Could not determine timezone")
                    self.timezone_verified = False
            else:
                print("❌ timedatectl command failed")
                self.timezone_verified = False
                
        except Exception as e:
            print(f"❌ Error checking timezone: {e}")
            self.timezone_verified = False
    
    def _check_ntp_sync(self):
        """Check NTP synchronization status"""
        try:
            result = subprocess.run(['timedatectl'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output = result.stdout
                
                ntp_sync = False
                last_sync = None
                
                if 'NTP synchronized:' in output:
                    ntp_line = [line for line in output.split('\n') if 'NTP synchronized:' in line][0]
                    ntp_status = ntp_line.split('NTP synchronized:')[1].strip().lower()
                    ntp_sync = ntp_status in ['yes', 'true']
                
                # Try to get last sync time
                try:
                    sync_result = subprocess.run(['systemctl', 'show', 'systemd-timesyncd', 
                                                '--property=ActiveEnterTimestamp'], 
                                               capture_output=True, text=True, timeout=3)
                    if sync_result.returncode == 0 and sync_result.stdout.strip():
                        last_sync = sync_result.stdout.split('=')[1].strip()
                except:
                    pass
                
                print(f"NTP Synchronized: {'Yes' if ntp_sync else 'No'}")
                if last_sync and last_sync != "n/a":
                    print(f"Last Sync: {last_sync}")
                
                if ntp_sync:
                    print("✅ Time is synchronized with internet time servers")
                    self.ntp_synced = True
                    self.last_ntp_sync_time = datetime.now()
                    self.ntp_loss_time = None
                    self.offline_mode = False
                else:
                    print("⚠️  NTP synchronization is currently unavailable")
                    print("   📡 Clock will use system time (may drift without internet)")
                    print("   🔄 Will continue monitoring for NTP restoration")
                    self.ntp_synced = False
                    if self.ntp_loss_time is None:
                        self.ntp_loss_time = datetime.now()
                    self.offline_mode = True
                    
        except Exception as e:
            print(f"❌ Error checking NTP sync: {e}")
            self.ntp_synced = False
            self.offline_mode = True
    
    def _verify_time_source(self):
        """Verify the time source we're using"""
        print("\nTime Source Configuration:")
        print("- Primary Source: datetime.now() (system clock)")
        print("- DST Handling: Automatic via system timezone database")
        print("- Offline Resilience: ✅ Continues with last known time")
        print("- NTP Recovery: ✅ Automatic background monitoring")
        
        # Test datetime.now() responsiveness
        start_time = time_module.time()
        test_time = datetime.now()
        end_time = time_module.time()
        response_time = (end_time - start_time) * 1000
        
        print(f"- datetime.now() response time: {response_time:.2f}ms")
        
        if response_time < 10:
            print("✅ Time source is responsive")
        else:
            print("⚠️  Time source response is slow")
    
    def _start_ntp_monitoring(self):
        """Start background thread to monitor NTP status"""
        if self.ntp_monitor_thread is None or not self.ntp_monitor_thread.is_alive():
            self.stop_monitoring = False
            self.ntp_monitor_thread = threading.Thread(target=self._ntp_monitor_loop, daemon=True)
            self.ntp_monitor_thread.start()
            print("🔄 Started background NTP monitoring")
    
    def _ntp_monitor_loop(self):
        """Background loop to monitor NTP status"""
        while not self.stop_monitoring:
            try:
                sleep(NTP_CHECK_INTERVAL)
                if self.stop_monitoring:
                    break
                    
                # Check current NTP status
                was_synced = self.ntp_synced
                self._check_ntp_status_quiet()
                
                # Log status changes
                if not was_synced and self.ntp_synced:
                    print(f"\n📡 NTP RESTORED at {datetime.now().strftime('%H:%M:%S')}")
                    print("✅ Clock re-synchronized with internet time servers")
                elif was_synced and not self.ntp_synced:
                    print(f"\n📡 NTP LOST at {datetime.now().strftime('%H:%M:%S')}")
                    print("⚠️  Continuing with system clock (offline mode)")
                    
            except Exception as e:
                # Silently continue monitoring
                pass
    
    def _check_ntp_status_quiet(self):
        """Check NTP status without printing (for background monitoring)"""
        try:
            result = subprocess.run(['timedatectl'], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                output = result.stdout
                ntp_sync = False
                
                if 'NTP synchronized:' in output:
                    ntp_line = [line for line in output.split('\n') if 'NTP synchronized:' in line][0]
                    ntp_status = ntp_line.split('NTP synchronized:')[1].strip().lower()
                    ntp_sync = ntp_status in ['yes', 'true']
                
                # Update status
                if ntp_sync and not self.ntp_synced:
                    self.ntp_synced = True
                    self.last_ntp_sync_time = datetime.now()
                    self.ntp_loss_time = None
                    self.offline_mode = False
                elif not ntp_sync and self.ntp_synced:
                    self.ntp_synced = False
                    if self.ntp_loss_time is None:
                        self.ntp_loss_time = datetime.now()
                    self.offline_mode = True
                    
        except:
            # If we can't check, assume offline
            if self.ntp_synced:
                self.ntp_synced = False
                if self.ntp_loss_time is None:
                    self.ntp_loss_time = datetime.now()
                self.offline_mode = True
    
    def monitor_dst_transition(self):
        """Monitor and log DST transitions"""
        if not self.startup_diagnostics_done:
            return
            
        current_dst = time_module.localtime().tm_isdst
        
        if self.last_dst_state is not None and current_dst != self.last_dst_state:
            # DST transition detected!
            now = datetime.now()
            transition_type = "Spring Forward" if current_dst else "Fall Back"
            
            print(f"\n🕐 DST TRANSITION DETECTED at {now.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   Type: {transition_type}")
            print(f"   DST Active: {current_dst}")
            
            self.dst_transitions_logged.append({
                'time': now,
                'type': transition_type,
                'dst_active': current_dst
            })
            
            self.last_dst_state = current_dst
    
    def get_verified_time(self):
        """Get current time with verification and offline awareness"""
        self.monitor_dst_transition()
        
        # Always return system time - it continues running even without NTP
        return datetime.now()
    
    def get_offline_duration(self):
        """Get how long we've been offline"""
        if self.ntp_loss_time and self.offline_mode:
            return datetime.now() - self.ntp_loss_time
        return None
    
    def print_status_summary(self):
        """Print a brief status summary with offline awareness"""
        dst_status = "Active" if time_module.localtime().tm_isdst else "Inactive"
        
        if self.offline_mode:
            offline_duration = self.get_offline_duration()
            if offline_duration:
                hours = int(offline_duration.total_seconds() // 3600)
                minutes = int((offline_duration.total_seconds() % 3600) // 60)
                duration_str = f"{hours}h{minutes}m" if hours > 0 else f"{minutes}m"
                status = f"🔶 OFFLINE {duration_str}"
            else:
                status = "🔶 OFFLINE"
        else:
            status = "✅ ONLINE"
        
        print(f"{status} | TZ: {'OK' if self.timezone_verified else 'CHECK'} | DST: {dst_status}")
    
    def stop_ntp_monitoring(self):
        """Stop the background NTP monitoring"""
        self.stop_monitoring = True
        if self.ntp_monitor_thread and self.ntp_monitor_thread.is_alive():
            self.ntp_monitor_thread.join(timeout=1)

# Initialize time manager
time_manager = TimeManager()

# Initialiseer LED-paneel
panel = None
try:
    panel = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS)
    panel.begin()
    print("✅ LED panel initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize LED panel: {e}")
    sys.exit(1)

# Constante kleur (R=255, G=255, B=100)
r, g, b = 255, 255, 100
color = Color(g, r, b)  # NeoPixel verwacht vaak GRB volgorde

# Cleanup function
def cleanup(signum=None, frame=None):
    """Clean up resources on exit"""
    print("\n🔄 Shutting down LED Word Clock...")
    
    # Stop NTP monitoring
    time_manager.stop_ntp_monitoring()
    
    if panel:
        print("💡 Turning off LEDs...")
        clear()
        update()
    
    print("👋 LED Word Clock stopped cleanly")
    sys.exit(0)

# Register signal handlers for clean exit
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# === Woordfuncties ===
def mfive():
    for i in [16, 17, 18, 19]:
        panel.setPixelColor(i, color)

def mten():
    for i in [1, 3, 4]:
        panel.setPixelColor(i, color)

def quarter():
    for i in [8, 9, 10, 11, 12, 13, 14]:
        panel.setPixelColor(i, color)

def twenty():
    for i in [1, 2, 3, 4, 5, 6]:
        panel.setPixelColor(i, color)

def half():
    for i in [20, 21, 22, 23]:
        panel.setPixelColor(i, color)

def past():
    for i in [25, 26, 27, 28]:
        panel.setPixelColor(i, color)

def to():
    for i in [28, 29]:
        panel.setPixelColor(i, color)

# Uuraanduidingen
def one():
    for i in [57, 60, 63]:
        panel.setPixelColor(i, color)

def two():
    for i in [48, 49, 57]:
        panel.setPixelColor(i, color)

def three():
    for i in [43, 44, 45, 46, 47]:
        panel.setPixelColor(i, color)

def four():
    for i in [56, 57, 58, 59]:
        panel.setPixelColor(i, color)

def five():
    for i in [32, 33, 34, 35]:
        panel.setPixelColor(i, color)

def six():
    for i in [40, 41, 42]:
        panel.setPixelColor(i, color)

def seven():
    for i in [40, 52, 53, 54, 55]:
        panel.setPixelColor(i, color)

def eight():
    for i in [35, 36, 37, 38, 39]:
        panel.setPixelColor(i, color)

def nine():
    for i in [60, 61, 62, 63]:
        panel.setPixelColor(i, color)

def ten():
    for i in [39, 47, 55]:
        panel.setPixelColor(i, color)

def eleven():
    for i in [50, 51, 52, 53, 54, 55]:
        panel.setPixelColor(i, color)

def twelve():
    for i in [48, 49, 50, 51, 53, 54]:
        panel.setPixelColor(i, color)

# Hulp-functies
def update():
    try:
        panel.show()
    except Exception as e:
        print(f"❌ Error updating display: {e}")

def clear():
    try:
        for i in range(panel.numPixels()):
            panel.setPixelColor(i, Color(0, 0, 0))
    except Exception as e:
        print(f"❌ Error clearing display: {e}")

def display_current_time(force_log=False):
    """Display the current time with enhanced time management"""
    # Get verified time (works offline too)
    now = time_manager.get_verified_time()
    hour = now.hour
    minute = now.minute
    
    clear()

    # Minuten in woorden
    if 3 <= minute <= 7:
        mfive()
        past()
    elif 8 <= minute <= 12:
        mten()
        past()
    elif 13 <= minute <= 17:
        quarter()
        past()
    elif 18 <= minute <= 22:
        twenty()
        past()
    elif 23 <= minute <= 27:
        twenty()
        mfive()
        past()
    elif 28 <= minute <= 32:
        half()
        past()
    elif 33 <= minute <= 37:
        twenty()
        mfive()
        to()
    elif 38 <= minute <= 42:
        twenty()
        to()
    elif 43 <= minute <= 47:
        quarter()
        to()
    elif 48 <= minute <= 52:
        mten()
        to()
    elif 53 <= minute <= 57:
        mfive()
        to()
    else:
        # Tussen :58 en :02, geen minuten aanduiding
        pass

    # Rond uur af indien na half
    if minute > 32:
        hour += 1
    hour = hour % 12
    if hour == 0:
        hour = 12

    # Uur in woorden
    if hour == 1:
        one()
    elif hour == 2:
        two()
    elif hour == 3:
        three()
    elif hour == 4:
        four()
    elif hour == 5:
        five()
    elif hour == 6:
        six()
    elif hour == 7:
        seven()
    elif hour == 8:
        eight()
    elif hour == 9:
        nine()
    elif hour == 10:
        ten()
    elif hour == 11:
        eleven()
    elif hour == 12:
        twelve()

    update()
    
    # Only log on specific events or every 5 minutes
    should_log = (
        force_log or 
        minute % 5 == 0 and now.second <= 3  # Every 5 minutes during first few seconds
    )
    
    if should_log:
        dst_indicator = " (DST)" if time_module.localtime().tm_isdst else " (STD)"
        connection_status = "📡" if not time_manager.offline_mode else "🔶"
        print(f"{connection_status} Display updated: {now.strftime('%H:%M:%S')}{dst_indicator}")

def startup_sequence():
    """Perform startup verification and setup"""
    print("🚀 Starting LED Word Clock with Offline Resilience")
    
    # Verify time system
    time_ok = time_manager.verify_system_time()
    
    if not time_ok:
        print("\n⚠️  WARNING: Critical time system configuration issues!")
        print("The clock requires correct timezone configuration.")
        response = input("Continue anyway? (y/N): ").lower()
        if response != 'y':
            print("Exiting. Please fix timezone configuration and restart.")
            sys.exit(1)
    
    print(f"\n🎯 Clock configured for timezone: {EXPECTED_TIMEZONE}")
    print(f"🔄 Refresh interval: {REFRESH_INTERVAL} seconds")
    print("📡 NTP monitoring: Background thread every 5 minutes")
    print("🔶 Offline mode: Continues with system clock when internet unavailable")
    print("📝 Terminal output: Every 5 minutes + important events only")
    print("⌨️  Press Ctrl+C to exit cleanly\n")
    
    # Show initial time with logging
    display_current_time(force_log=True)

# === Hoofdlus ===
def main():
    # Run startup sequence
    startup_sequence()
    
    status_counter = 0
    
    while True:
        try:
            # Display current time (works online and offline)
            display_current_time()
            
            # Show status summary every 300 iterations (roughly every 5 minutes)
            status_counter += 1
            if status_counter >= 300:
                time_manager.print_status_summary()
                status_counter = 0
            
            sleep(REFRESH_INTERVAL)
            
        except KeyboardInterrupt:
            cleanup()
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            print("⏳ Retrying in 5 seconds...")
            sleep(5)

if __name__ == "__main__":
    main()
