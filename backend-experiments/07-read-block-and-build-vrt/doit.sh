#!/usr/bin/env sh

for i in {1..10}
do
    /usr/bin/time --portability gdalbuildvrt -overwrite nlud.vrt /vsigs/natcap-urban-online-datasets/nlud.tif
done
