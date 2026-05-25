#!/bin/bash
# 在 GitHub 网页添加 SSH 公钥后运行此脚本
set -e
cd "$(dirname "$0")"
echo "正在推送到 GitHub..."
git push -u origin main
echo "完成！请打开: https://github.com/zhoumingjun0804-rgb/aizhushou_Age"
