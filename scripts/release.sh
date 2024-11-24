#!/bin/bash

# 检查是否提供版本号参数
if [ -z "$1" ]; then
    echo "请提供版本号，例如: ./release.sh 1.0.1"
    exit 1
fi

VERSION=$1

# 更新版本号
echo $VERSION > VERSION

# 更新 CHANGELOG.md
DATE=$(date +%Y-%m-%d)
echo "请编辑 CHANGELOG.md 添加版本 $VERSION 的更新说明"
read -p "按回车继续..."

# 提交变更
git add VERSION CHANGELOG.md
git commit -m "Release version $VERSION"
git tag -a "v$VERSION" -m "Version $VERSION"

# 构建发布包
python setup.py sdist bdist_wheel

echo "版本 $VERSION 发布完成！"
echo "请执行以下命令推送到远程仓库："
echo "git push origin master"
echo "git push origin v$VERSION"
echo "python -m twine upload dist/*" 