Java.perform(function () {
    console.log("[*] Injecting Frida bypass hooks...");

    var Build = Java.use("android.os.Build");

    // Spoof common build properties that expose emulators
    Build.FINGERPRINT.value = "google/oriole/oriole:12/SD1A.210817.037/7805805:user/release-keys";
    Build.MODEL.value = "Pixel 6";
    Build.MANUFACTURER.value = "Google";
    Build.BRAND.value = "google";
    Build.BOARD.value = "oriole";
    Build.HARDWARE.value = "oriole";
    Build.PRODUCT.value = "oriole";
    Build.DEVICE.value = "oriole";
    Build.TAGS.value = "release-keys";

    console.log("[+] Spoofed android.os.Build properties");

    // Intercept java.io.File to hide root/emulator artifacts
    var File = Java.use("java.io.File");
    var filePathsToHide = [
        "/system/app/Superuser.apk",
        "/sbin/su",
        "/system/bin/su",
        "/system/xbin/su",
        "/data/local/xbin/su",
        "/data/local/bin/su",
        "/system/sd/xbin/su",
        "/system/bin/failsafe/su",
        "/data/local/su",
        "/su/bin/su",
        "/system/etc/security/otacerts.zip",
        "/system/lib/libc_malloc_debug_qemu.so",
        "/sys/qemu_trace",
        "/system/bin/qemu-props"
    ];

    File.exists.implementation = function () {
        var name = this.getAbsolutePath();
        for (var i = 0; i < filePathsToHide.length; i++) {
            if (name === filePathsToHide[i]) {
                console.log("[+] Blocked File.exists check for: " + name);
                return false;
            }
        }
        return this.exists();
    };
    
    File.getAbsolutePath.implementation = function() {
        var name = this.getAbsolutePath();
        if (name.indexOf("libhoudini") !== -1 || name.indexOf("libndk") !== -1) {
             console.log("[+] Masking native bridge file check: " + name);
             return "/data/data/hidden";
        }
        return name;
    }

    console.log("[*] Bypass hooks injected successfully.");
});
