"""抢 Oracle Always Free ARM 实例：VM.Standard.A1.Flex。

只改下面几行常量，其它逻辑都在 oci_common.py 里。
"""
from oci_common import retry

SHAPE = "VM.Standard.A1.Flex"

# 规格阶梯：按顺序轮换尝试。
# 2C/12G 是 Always Free 上限；抢不到时会试 1C/6G —— 小规格在拥挤的区域命中率高得多。
# 只想抢 2C/12G 就改成：SPECS = [(2, 12)]
SPECS = [(2, 12), (1, 6)]

BOOT_VOLUME_SIZE_IN_GBS = 100   # Always Free 区域总额度 200 GB，两台别都设 200
INSTANCE_NAME = "instance-rcc-2026"

if __name__ == "__main__":
    retry(SHAPE, SPECS, BOOT_VOLUME_SIZE_IN_GBS, INSTANCE_NAME)
