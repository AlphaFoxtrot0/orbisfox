#!/bin/sh
# Builds plugin/dist/OrbisFoxBridge.jar without Gradle:  ./build.sh /path/to/HytaleServer.jar
set -e
here=$(cd "$(dirname "$0")" && pwd)
[ -n "$1" ] || { echo "usage: $0 /path/to/HytaleServer.jar"; exit 1; }
rm -rf "$here/build/classes" && mkdir -p "$here/build/classes" "$here/dist"
javac --release 25 -encoding UTF-8 -cp "$1" -d "$here/build/classes" $(find "$here/src/main/java" -name '*.java')
cp -r "$here/src/main/resources/." "$here/build/classes/"
jar --create --file "$here/dist/OrbisFoxBridge.jar" -C "$here/build/classes" .
echo "Built plugin/dist/OrbisFoxBridge.jar"
