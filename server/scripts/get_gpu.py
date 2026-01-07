# pip install pyopencl

import subprocess

def check_vulkan_support():
    try:
        # Run vulkaninfo and capture its output
        result = subprocess.check_output(['vulkaninfo', '--summary'], stderr=subprocess.STDOUT, text=True)
        print('Vulkan is installed and working.')
        
        # Look for specific GPU names in the output
        if "NVIDIA" in result:
            print("Vulkan is using the NVIDIA driver.")
        elif "AMD" in result:
            print("Vulkan is using the AMD (Mesa) driver.")
        elif "llvmpipe" in result or "softpipe" in result:
            print("Vulkan is using a software (CPU) implementation (performance will be low).")
        else:
            print("Vulkan is using an unknown/other driver.")
        
        return True

    except (subprocess.CalledProcessError, FileNotFoundError):
        print('Vulkan not installed or drivers not properly configured. "vulkaninfo" command not found.')
        # Provide instructions for the user to install tools
        print('Please install vulkan-tools (e.g., sudo apt install vulkan-tools on Ubuntu) to use this feature.')
        return False

# Example usage
is_vulkan_available = check_vulkan_support()


