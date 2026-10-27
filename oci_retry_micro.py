"""抢 Oracle Always Free x86 实例：VM.Standard.E2.1.Micro（每个租户最多 2 台）。

Micro 一般立刻就有容量，适合先拿一台能用的机器。
抢第二台时把 INSTANCE_NAME 改成 micro-2。
"""
from oci_common import retry

SHAPE = "VM.Standard.E2.1.Micro"
SPECS = []                      # Micro 是固定规格，不能调 OCPU/内存
BOOT_VOLUME_SIZE_IN_GBS = 50
INSTANCE_NAME = "micro-1"

if __name__ == "__main__":
    retry(SHAPE, SPECS, BOOT_VOLUME_SIZE_IN_GBS, INSTANCE_NAME)
