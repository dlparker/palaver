# pip install pyopencl
import pyopencl as cl

for plat in cl.get_platforms():
    for dev in plat.get_devices():
        info = {
            "platform": plat.name,
            "vendor": dev.vendor,
            "model": dev.name,
            "driver": dev.driver_version,
            "global_mem_mib": dev.global_mem_size // (1024 ** 2),
        }
        print(info)   
