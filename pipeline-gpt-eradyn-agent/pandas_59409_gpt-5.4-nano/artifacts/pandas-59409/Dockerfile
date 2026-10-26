# pandas-59409 — NumBugBench instance, rebuild recipe
#
# TRANSPARENCY ARTIFACT, NOT THE BUILD THAT PRODUCED THE IMAGE.
#
# The published image is a snapshot of the container in which the reproduction
# was validated, so its environment is the one that actually produced
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS.
# Re-running the steps below resolves dependencies again and may therefore land
# on a DIFFERENT environment — which is the drift this packaging exists to
# avoid. Use this file to understand or modify the instance; use the image to
# reproduce it.
#
#   docker run numbugbench/pandas-59409:eradyn-agent-gpt validate
#
FROM python:3.11.7

# both revisions, as the reproduction left them
#   /workspace/buggy  5591ef31e08b798bea5309c4adaee20423c5e371   (+ the developer test patch applied)
#   /workspace/fixed  c831ccda407b854962c0d85b6d42e47fa0dee015   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.11.7 (main, Jan  8 2024, 05:55:31) [Clang 17.0.6 ]
#   numpy    2.4.6
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
