# cof-crest 镜像：CREST 构象搜索（conda-forge crest + xtb，Linux/amd64）
# 用途：Windows 本机无法直接安装 CREST（conda 无 win-64 包、pip 无 wheel），
#       经 Docker Desktop(WSL2) 以容器运行，由 src/dft/conformers.py 自动挂载调用。
# 构建：docker build -t cof-crest:latest .
# 验证：docker run --rm cof-crest:latest crest --version
FROM condaforge/miniforge3:latest

# conda-forge 的 crest 包自带 xtb / xtb-python 依赖（Linux x86_64 可用）
RUN conda install -y -c conda-forge crest xtb \
    && conda clean -afy

ENV PATH=/opt/conda/bin:$PATH
# xtb 参数文件位置（容器内直接执行 crest/xtb 时必需，等效 conda activate）
ENV XTBPATH=/opt/conda/share/xtb
ENV OMP_NUM_THREADS=4
ENV OMP_STACKSIZE=1G

# 宿主工作目录挂载点：crest 在 /work 下读写 input.xyz / crest_conformers.xyz
WORKDIR /work

CMD ["crest", "--version"]
