#!/bin/bash
set -e

SWAP_SIZE="2G"
SWAP_FILE="/swapfile"

echo "=== กำลังตรวจสอบและตั้งค่า Swap $SWAP_SIZE บน Ubuntu ==="

# ตรวจสอบสิทธิ์ root
if [ "$EUID" -ne 0 ]; then
  echo "[!] กรุณารันด้วยสิทธิ์ root หรือใช้ sudo (เช่น sudo bash setup_swap.sh)"
  exit 1
fi

# ตรวจสอบว่ามี swapfile เดิมอยู่หรือไม่
if [ -f "$SWAP_FILE" ]; then
  echo "[-] พบ $SWAP_FILE อยู่แล้ว กำลังปิด swap เดิมเพื่อปรับขนาดใหม่..."
  swapoff "$SWAP_FILE" 2>/dev/null || true
  rm -f "$SWAP_FILE"
fi

echo "[+] กำลังสร้าง $SWAP_FILE ขนาด $SWAP_SIZE..."
if ! fallocate -l "$SWAP_SIZE" "$SWAP_FILE" 2>/dev/null; then
  # fallback ใช้ dd หาก filesystem ไม่รองรับ fallocate
  dd if=/dev/zero of="$SWAP_FILE" bs=1M count=2048 status=progress
fi

echo "[+] กำหนดสิทธิ์ chmod 600..."
chmod 600 "$SWAP_FILE"

echo "[+] กำลัง format เป็น swap..."
mkswap "$SWAP_FILE"

echo "[+] กำลังเปิดใช้งาน swap..."
swapon "$SWAP_FILE"

# ตรวจสอบ /etc/fstab เพื่อให้บูตแล้ว swap ยังอยู่ (ป้องกันการเขียนซ้ำ)
if ! grep -q "$SWAP_FILE" /etc/fstab; then
  echo "[+] บันทึกลง /etc/fstab เพื่อเปิดใช้อัตโนมัติตอนบูต..."
  echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
else
  echo "[i] $SWAP_FILE มีอยู่ใน /etc/fstab แล้ว"
fi

# ปรับค่า swappiness ให้เหมาะสม (20 สำหรับเซิร์ฟเวอร์ ให้เน้นใช้ RAM จริงก่อน ค่อยดึง swap เมื่อจำเป็น)
sysctl vm.swappiness=20
if ! grep -q "vm.swappiness" /etc/sysctl.conf; then
  echo "vm.swappiness=20" >> /etc/sysctl.conf
fi

echo "========================================="
echo "✅ ตั้งค่า Swap เรียบร้อยแล้ว!"
echo "สถานะ RAM และ Swap ปัจจุบัน:"
free -h
echo "========================================="
