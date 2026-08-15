# Campus Native Agent 前端镜像（M6）：node 构建 → nginx 托管静态文件 + /api 反代后端
# 国内 npm 慢：npmmirror registry（npm ci 锁 package-lock.json）

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com \
    && npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
