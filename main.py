import os
import asyncio
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import MessagingApi, ApiClient, Configuration
from linebot.v3.webhooks import MessageEvent, TextMessageContent, JoinEvent, LeaveEvent
from linebot.v3.messaging import TextMessage
from linebot.v3.exceptions import InvalidSignatureError
import discord
from discord.ext import commands
import threading

# 修改全域字典，用來儲存所有 Line 群組資訊
line_groups = {
    'default': os.getenv('LINE_GROUP_ID'),
    'active_groups': {
        os.getenv('LINE_GROUP_ID'): {
            'id': os.getenv('LINE_GROUP_ID'),
            'name': 'Default Group'
        }
    } if os.getenv('LINE_GROUP_ID') else {}
}

# 設定 Discord 機器人
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 修改 Line Bot 設定
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
line_bot_api = MessagingApi(ApiClient(configuration))

# 設定 Flask
app = Flask(__name__)

@app.route("/", methods=['GET'])
def hello():
    return 'Bot is running!', 200

# Discord -> Line
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    if message.channel.id == int(os.getenv('DISCORD_CHANNEL_ID')):
        try:
            if line_groups['active_groups']:
                for group in line_groups['active_groups'].values():
                    if group and 'id' in group:
                        app.logger.info(f"發送訊息到群組：{group['id']}")
                        line_bot_api.push_message(
                            to=group['id'],
                            messages=[TextMessage(text=f"Discord - {message.author.name}: {message.content}")]
                        )
            else:
                app.logger.warning("沒有活躍的 Line 群組")
        except Exception as e:
            app.logger.error(f"發送到 Line 時發生錯誤：{str(e)}")

# Line -> Discord
@app.route("/callback", methods=['POST'])
def callback():
    # 獲取 X-Line-Signature header 值
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    app.logger.info(f"收到 webhook 請求，簽名：{signature}")
    app.logger.info(f"請求內容：{body}")
    
    try:
        # 確認 Channel Secret 是否正確設定
        app.logger.info(f"使用的 Channel Secret: {os.getenv('LINE_CHANNEL_SECRET')[:5]}...")
        
        # 驗證簽名
        handler.handle(body, signature)
        
    except InvalidSignatureError:
        app.logger.error(f"簽名驗證失敗")
        app.logger.error(f"收到的簽名: {signature}")
        abort(400)
        
    except Exception as e:
        app.logger.error(f"處理 webhook 時發生錯誤：{str(e)}")
        return str(e), 500
        
    return 'OK', 200

@handler.add(MessageEvent)
def handle_message(event):
    if isinstance(event.message, TextMessageContent):
        try:
            if event.source.type == 'group':
                group_id = event.source.group_id
                # 取得發送者資訊
                profile = line_bot_api.get_group_member_profile(
                    group_id=group_id,
                    user_id=event.source.user_id
                )
                user_name = profile.display_name
                
                # 更新群組資訊
                if group_id not in line_groups['active_groups']:
                    group_summary = line_bot_api.get_group_summary(group_id=group_id)
                    line_groups['active_groups'][group_id] = {
                        'id': group_id,
                        'name': group_summary.group_name
                    }
                    app.logger.info(f"新增群組：{group_summary.group_name}")
                
                # 發送到 Discord
                channel = bot.get_channel(int(os.getenv('DISCORD_CHANNEL_ID')))
                if channel:
                    message_text = f"Line - {user_name}: {event.message.text}"
                    future = asyncio.run_coroutine_threadsafe(
                        channel.send(message_text),
                        bot.loop
                    )
                    future.result()
                    app.logger.info(f"已發送到 Discord: {message_text}")
                
        except Exception as e:
            app.logger.error(f"處理 Line 訊息時發生錯誤：{str(e)}")

@handler.add(JoinEvent)
def handle_join(event):
    if event.source.type == 'group':
        group_id = event.source.group_id
        if group_id not in line_groups['active_groups']:
            line_groups['active_groups'][group_id] = {
                'id': group_id,
                'name': line_bot_api.get_group_summary(group_id).group_name
            }
            print(f"已加入新群組：{group_id}")
        else:
            print(f"群組已存在：{group_id}")

@handler.add(LeaveEvent)
def handle_leave(event):
    if event.source.type == 'group':
        group_id = event.source.group_id
        # 離開群組時從清單中移除
        if group_id in line_groups['active_groups']:
            del line_groups['active_groups'][group_id]
            print(f"已離開群組：{group_id}")

@bot.event
async def on_ready():
    print(f'Discord 機器人已登入為 {bot.user}')
    try:
        channel = bot.get_channel(int(os.getenv('DISCORD_CHANNEL_ID')))
        if channel:
            await channel.send("機器人已上線！")
            # 初始化預設群組
            if os.getenv('LINE_GROUP_ID'):
                try:
                    group_summary = line_bot_api.get_group_summary(
                        group_id=os.getenv('LINE_GROUP_ID')
                    )
                    line_groups['active_groups'][os.getenv('LINE_GROUP_ID')] = {
                        'id': os.getenv('LINE_GROUP_ID'),
                        'name': group_summary.group_name
                    }
                    await channel.send(f"已連接到 Line 群組：{group_summary.group_name}")
                    app.logger.info(f"已初始化預設群組：{group_summary.group_name}")
                except Exception as e:
                    app.logger.error(f"初始化群組時發生錯誤：{str(e)}")
    except Exception as e:
        app.logger.error(f"機器人初始化時發生錯誤：{str(e)}")

# 啟動 Discord 機器人
def run_discord_bot():
    bot.run(os.getenv('DISCORD_TOKEN'))

# 新增一個測試路由
@app.route("/callback", methods=['GET'])
def callback_test():
    return 'Webhook is working!', 200

if __name__ == "__main__":
    # 啟動 Discord bot
    discord_thread = threading.Thread(target=run_discord_bot, daemon=True)
    discord_thread.start()
    
    # 啟動 Flask
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port) 