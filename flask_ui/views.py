# all the imports
import pandas as pd
import numpy as np
import psycopg2

from flask import request, session, g, redirect, url_for, \
     abort, render_template, flash
from wtforms import Form, validators, TextField, BooleanField
from wtforms.fields.html5 import DateField

from flask_ui import app, db, engine
from models import Customer, CustomerWithMarket, CustomerDemand, \
    Market, Premium, Parameter

def connect_db():
    import urlparse

    urlparse.uses_netloc.append("postgres")
    url = urlparse.urlparse(app.config['SQLALCHEMY_DATABASE_URI'])
    return psycopg2.connect(database=url.path[1:],
                            user=url.username,
                            password=url.password,
                            host=url.hostname,
                            port=url.port)

@app.before_request
def before_request():
    g.db = connect_db()

@app.teardown_request
def teardown_request(exception):
    db = getattr(g, 'db', None)
    if db is not None:
        db.close()

@app.route('/')
@app.route('/index/<int:page>')
def show_customers(page=1):
    customers = CustomerWithMarket.query.paginate(page,
                                                  app.config['CUSTOMERS_PER_PAGE'],
                                                  False)
    markets = Market.query.all()
    return render_template('show_customers.html',
                           customers=customers,
                           markets=markets)

@app.route('/add_customer', methods=['POST'])
def add_customer():
    from StringIO import StringIO
    from generators import generate_customer_demand_image, generate_random_customer_data
    if not session.get('logged_in'):
        abort(401)
    demand = generate_random_customer_data()
    image64 = generate_customer_demand_image(demand)

    new_customer = Customer(name=request.form['name'],
                            market_id=request.form['market_id'],
                            image64=image64)
    db.session.add(new_customer)
    db.session.commit()
    ids = np.array(range(len(demand)))
    ids.fill(new_customer.customer_id)
    demand_data = pd.DataFrame({'customer_id': ids,
                                'datetime': demand.index,
                                'value': demand.values})
    demand_buffer = StringIO()
    demand_data.to_csv(demand_buffer, header=False, index=False)
    demand_buffer.seek(0)
    cur = engine.raw_connection().cursor()
    cur.copy_from(demand_buffer, 'retail.customer_demand', sep=',')
    cur.connection.commit()
    # add push to db demand table here
    flash('New customer was successfully added')
    return redirect(url_for('show_customers'))

@app.route('/generate_customer_premium/<int:customer_id>', methods=['GET', 'POST'])
def generate_customer_premium(customer_id):
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    from tasks import generate_premium
    if not session.get('logged_in'):
        abort(401)
    form = premium_parameters_form(request.form)
    if request.method == "POST" and form.validate():
        contract_end = []
        if form.contract12:
            contract_end.append(form.contract_start.data + relativedelta(months=12+1, days=-1))
        #if form.contract24:
        #    contract_end.append(form.contract_start + relativedelta(months=12*2+1, days=-1))
        #if form.contract36:
        #    contract_end.append(form.contract_start + relativedelta(months=12*3+1, days=-1))
        contract_start = [form.contract_start for x in range(len(contract_end))]
        valuation_date = datetime.today()
        customer = Customer.query.filter(Customer.customer_id==customer_id).one()
        parameters = fetch_run_parameters(customer.market_id)
        run_id = parameters.run_id
        contract_start_date=form.contract_start.data
        contract_end_date=datetime(*(contract_end[0].timetuple()[:6]))
        result = generate_premium.delay(customer_id=customer_id,
                                        run_id=run_id,
                                        contract_start_date=contract_start_date,
                                        contract_end_date=contract_end_date,
                                        valuation_date=valuation_date)
        if result:
            flash('Premium has been queued for generation')
        return redirect(url_for('display_customer_premiums', customer_id=customer_id))
    customer = CustomerWithMarket.query.filter(CustomerWithMarket.customer_id==customer_id).one()
    return render_template('generate_customer_premium.html',
                           form=form,
                           customer=customer)

class premium_parameters_form(Form):
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    default_start = datetime.today() + relativedelta(months=1, day=1)
    contract_start = DateField(label="contract_start",
                               default=default_start)
    #choices = [(None, '0')] + [(x, str(x)) for x in range(1,36)]
    #contract_adhoc = ChoiceField(label='ad hoc months', choices=choices, required=False)
    contract12 = BooleanField(label="12 months", default=True)
    contract24 = BooleanField(label="24 months")
    contract36 = BooleanField(label="36 months")
    email = TextField(label='Email',
                      default='mapdes@gmail.com',
                      validators=[validators.Email(message='Invalid email address')])

@app.route('/display_customer_premiums/<int:customer_id>/<int:page>')
@app.route('/display_customer_premiums/<int:customer_id>')
def display_customer_premiums(customer_id, page=1):
    if not session.get('logged_in'):
        abort(401)
    customer = CustomerWithMarket.query.filter(CustomerWithMarket.customer_id==customer_id).one()
    premiums = Premium.query.filter(Premium.customer_id==customer_id)
    premiums = premiums.paginate(page,
                                 app.config['PREMIUMS_PER_PAGE'],
                                 False)
    return render_template('display_customer_premiums.html',
                           customer=customer,
                           premiums=premiums)

@app.route('/display_customer/<int:customer_id>/<int:page>')
@app.route('/display_customer/<int:customer_id>')
def display_customer(customer_id, page=1):
    if not session.get('logged_in'):
        abort(401)
    customer = CustomerWithMarket.query.filter(CustomerWithMarket.customer_id==customer_id).one()
    customer_demand = CustomerDemand.query.filter(CustomerDemand.customer_id==customer_id)
    customer_demand = customer_demand.paginate(page,
                                               app.config['DEMAND_ITEMS_PER_PAGE'],
                                               False)
    return render_template('display_customer.html',
                           customer_demand=customer_demand,
                           customer=customer)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] != app.config['USERNAME']:
            error = 'Invalid username'
        elif request.form['password'] != app.config['PASSWORD']:
            error = 'Invalid password'
        else:
            session['logged_in'] = True
            flash('You were logged in')
            return redirect(url_for('show_customers'))
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('You were logged out')
    return redirect('/')

def fetch_run_parameters(market_id):
    parameters = Parameter.query.filter(Parameter.market_id==market_id).order_by(Parameter.db_upload_date).first()
    return parameters

@app.errorhandler(404)
def internal_error(error):
    return render_template('404.html'), 404
