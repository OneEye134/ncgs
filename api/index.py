from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/OUR.SCHOOL.LIFE')
def home():
    return render_template('osl.html')